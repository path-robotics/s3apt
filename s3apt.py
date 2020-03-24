from __future__ import print_function

import config
import json
import urllib
import boto3
import botocore
import tempfile
import tarfile
import debian.arfile
import hashlib
import re
import sys
import os
from botocore.client import Config


def checksums(fname):

    fh = open(fname, "rb")

    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()

    size = 1024 * 1024
    while True:
        dat = fh.read(size)
        md5.update(dat)
        sha1.update(dat)
        sha256.update(dat)
        if len(dat) < size:
            break

    fh.close()

    return md5.hexdigest(), sha1.hexdigest(), sha256.hexdigest()


def get_control_data(debfile):
    ar = debian.arfile.ArFile(debfile)

    control_member = list(filter(lambda x: x.startswith('control.tar'), ar.getnames()))[0]
    control_fh = ar.getmember(control_member)

    tar_file = tarfile.open(fileobj=control_fh, mode='r')

    # control file can be named different things
    control_file_name = [x for x in tar_file.getmembers() if x.name in ['control', './control']][0]

    control_data = str(tar_file.extractfile(control_file_name).read().strip(), encoding='utf-8')
    # Strip out control fields with blank values.  This tries to allow folded
    # and multiline fields to pass through.  See the debian policy manual for
    # more info on folded and multiline fields.
    # https://www.debian.org/doc/debian-policy/ch-controlfields.html#s-binarycontrolfiles
    lines = control_data.strip().split("\n")
    filtered = []
    for line in lines:
        # see if simple field
        if re.search(r"^\w[\w\d_-]+\s*:", line):
            k, v = line.split(':', 1)
            if v.strip() != "":
                filtered.append(line)
        else:
            # otherwise folded or multiline, just pass it through
            filtered.append(line)

    return "\n".join(filtered)

def format_package_record(ctrl, fname):
    pkgrec = ctrl.strip().split("\n")

    stat = os.stat(fname)
    pkgrec.append("Size: %d" % (stat.st_size))

    md5, sha1, sha256 = checksums(fname)
    pkgrec.append("MD5sum: %s" % (md5))
    pkgrec.append("SHA1: %s" % (sha1))
    pkgrec.append("SHA256: %s" % (sha256))

    return "\n".join(pkgrec)


def read_control_data(deb_obj):
    """
    Downloads the .deb file and reads the debian control data out of it.
    This also adds in packaging data (md5, sha, etc) that is not in control
    files.
    """
    print("Creating cached control data for: %s" % (str(deb_obj)))

    fd, tmp = tempfile.mkstemp()
    fh = os.fdopen(fd, "wb")
    s3fh = deb_obj.get()['Body']
    size = 1024*1024
    while True:
        dat = s3fh.read(size)
        fh.write(dat)
        if len(dat) < size:
            break
    fh.close()
    #os.close(fd)

    try:
        ctrl = get_control_data(tmp)
        pkg_rec = format_package_record(ctrl, tmp)
        #print(pkg_rec)
        return pkg_rec
    finally:
        os.remove(tmp)


def get_cached_control_data(deb_obj):
    """
    Get cached control file information about a debian package, or build it if
    this is the first time.
    """
    s3 = boto3.resource('s3')
    etag = deb_obj.e_tag.strip('"')

    cache_obj = s3.Object(bucket_name=config.APT_REPO_BUCKET_NAME, key=config.CONTROL_DATA_CACHE_PREFIX + '/' + etag)
    exists = True
    try:
        control_data = str(cache_obj.get()['Body'].read(), encoding='utf-8')
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            exists = False
        else:
            raise(e)

    if not exists:
        control_data = read_control_data(deb_obj)
        cache_obj.put(Body=control_data)

    return control_data

def get_package_index_hash(prefix):
    """
    Returns the md5 hash of the names of all the packages in the index. This can be used
    to detect if all the packages are represented without having to load a control data cache
    file for each package.
    """
    s3 = boto3.resource('s3')
    try:
        print("looking for existing Packages file: %sPackages" % prefix)
        package_index_obj = s3.Object(bucket_name=config.APT_REPO_BUCKET_NAME, key=prefix + 'Packages')
        return package_index_obj.metadata.get('packages-hash', None)
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            return None
        else:
            raise(e)

def calc_package_index_hash(deb_names):
    """
    Calculates a hash of all the given deb file names. This is deterministic so
    we can use it for short-circuiting.
    """
    md5 = hashlib.md5()
    md5.update(bytes("\n".join(sorted(deb_names)), encoding='utf-8'))
    return md5.hexdigest()

def rebuild_package_index(prefix):
    # Get all .deb keys in directory
    # Get the cache entry
    # build package file
    deb_names = []
    deb_objs = []

    filter_prefix = prefix + '/'

    print("REBUILDING PACKAGE INDEX: %s" % (prefix))
    s3 = boto3.resource('s3')
    for obj in s3.Bucket(config.APT_REPO_BUCKET_NAME).objects.filter(Prefix=filter_prefix):
        if not obj.key.endswith(".deb"):
            continue
        deb_objs.append(obj)
        deb_names.append(obj.key.split('/')[-1])

    if not len(deb_objs):
        print("NOT BUILDING EMPTY PACKAGE INDEX")
        return

    # See if we need to rebuild the package index
    metadata_pkghash = get_package_index_hash(filter_prefix)
    calcd_pkghash = calc_package_index_hash(deb_names)
    print("calcd_pkghash=%s, metadata_pkghash=%s" % (calcd_pkghash, metadata_pkghash))
    if metadata_pkghash == calcd_pkghash:
        print("PACKAGE INDEX ALREADY UP TO DATE")
        return

    pkginfos = []
    for obj in deb_objs:
        print(obj.key)

        pkginfo = get_cached_control_data(obj)
        pkginfo = pkginfo + "\n%s\n" % ("Filename: %s" % obj.key)
        pkginfos.append(pkginfo)

    package_index_obj = s3.Object(bucket_name=config.APT_REPO_BUCKET_NAME, key=prefix + "/Packages")
    print("Writing package index: %s" % (str(package_index_obj)))
    package_index_obj.put(Body="\n".join(sorted(pkginfos)), Metadata={'packages-hash': calcd_pkghash})

    print("DONE REBUILDING PACKAGE INDEX")

def delete_new_versions(prefix, key):
    s3 = boto3.client('s3')
    keys = ["Versions", "DeleteMarkers"]
    results = []
    for k in keys:
        response = s3.list_object_versions(Bucket=config.APT_REPO_BUCKET_NAME, Prefix=prefix)
        if k in response:
            response = response[k]
        else:
            continue
        versions = [{"VersionId": r["VersionId"], "LastModified": r["LastModified"]} for r in response if r["Key"] == key]
        results.extend(versions)
    print("results", results)
    if len(results) <= 1:
        return False
    sorted_versions = sorted(
        results,
        key=lambda x: x['LastModified']
    )
    print("sorted results", sorted_versions)
    # Dont delete the oldest version
    sorted_versions.pop(0)
    print("minus oldest", sorted_versions)
    s3.delete_objects(Bucket=config.APT_REPO_BUCKET_NAME,
                      Delete={'Objects': [{"Key": key, "VersionId": v["VersionId"]} for v in sorted_versions]})
    return True

## Lambda Entry Points

def lambda_handler(event, context):
    print("LAMBDA HANDLER - APT REPO SYNC")
    print("Received event: " + json.dumps(event, indent=2))

    # Explicit event request to rebuild package index.  If the lambda is configured
    # correctly this shouldn't really be needed.
    if event.get('action', None) == 'rebuild_package_index':
        event['prefix'] = event['prefix'].strip('/')
        return rebuild_package_index(event['prefix'])

    # Get the object from the event and show its content type
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = urllib.unquote_plus(event['Records'][0]['s3']['object']['key']).decode('utf8')


    # If the Packages index changed or was deleted, try again to rebuild it to
    # make sure it is up to date.
    if key.endswith("/Packages"):
        prefix = "/".join(key.split('/')[0:-1])
        return rebuild_package_index(prefix)

    # If a deb was uploaded
    if key.endswith(".deb") and event['Records'][0]['eventName'].startswith('ObjectCreated'):
        s3 = boto3.resource('s3')
        deb_obj = s3.Object(bucket_name=bucket, key=key)
        print("S3 Notification of new key. Ensuring cached control data exists: %s" % (str(deb_obj)))
        get_cached_control_data(deb_obj)

    # If a package inside this bucket was updated delete the new versions, otherwise (added or deleted), rebuild the index.
    if bucket == config.APT_REPO_BUCKET_NAME and key.endswith(".deb"):
        prefix = "/".join(key.split('/')[0:-1])
        if not delete_new_versions(prefix, key):
            rebuild_package_index(prefix)

    print("DONE")



if __name__ == "__main__":
    fname = sys.argv[1]

    ctrl = get_control_data(fname)
    pkg_rec = format_package_record(ctrl, fname)
    print(pkg_rec)
