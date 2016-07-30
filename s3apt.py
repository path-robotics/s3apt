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

    control_fh = ar.getmember('control.tar.gz')

    tar_file = tarfile.open(fileobj=control_fh, mode='r:gz')

    control_data = tar_file.extractfile("./control").read().strip()
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

    return "fake control data.."



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
        control_data = cache_obj.get()['Body'].read()
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            exists = False
        else:
            raise(e)
    
    if not exists:
        control_data = read_control_data(deb_obj)        
        cache_obj.put(Body=control_data)

    return control_data
    

def rebuild_package_index(prefix):
    # Get all .deb keys in directory
    # Get the cache entry
    # build package file
    packages = []
    filter_prefix = prefix
    if not filter_prefix.endswith('/'):
        filter_prefix += '/'

    print("REBUILDING PACKAGE INDEX: %s" % (prefix))
    s3 = boto3.resource('s3')
    for obj in s3.Bucket(config.APT_REPO_BUCKET_NAME).objects.filter(Prefix=filter_prefix):
        if not obj.key.endswith(".deb"):
            continue
        print(obj.key)   

        pkginfo = get_cached_control_data(obj)    
        pkginfo = pkginfo + "\n%s\n" % ("Filename: %s" % obj.key)
        packages.append(pkginfo)

    
    package_index_obj = s3.Object(bucket_name=config.APT_REPO_BUCKET_NAME, key=prefix + "/Packages")
    print("Writing package index: %s" % (str(package_index_obj)))
    package_index_obj.put(Body="\n".join(sorted(packages)))

    print("DONE REBUILDING PACKAGE INDEX")
    

## Lambda Entry Points

def lambda_handler(event, context):
    print("LAMBDA HANDLER - APT REPO SYNC")
    print("Received event: " + json.dumps(event, indent=2))

    if event.get('action', None) == 'rebuild_package_index':
        event['prefix'] = event['prefix'].strip('/')
        return rebuild_package_index(event['prefix'])

    # Get the object from the event and show its content type
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = urllib.unquote_plus(event['Records'][0]['s3']['object']['key']).decode('utf8')

    # For S3 notifications, just make sure we have a cached copy of the control
    # data.
    
    s3 = boto3.resource('s3')
    deb_obj = s3.Object(bucket_name=bucket, key=key)

    print("S3 Notification of new key. Ensuring cached control data exists: %s" % (str(deb_obj)))
    get_cached_control_data(deb_obj)
    
    print("DONE")



if __name__ == "__main__":
    fname = sys.argv[1]
    print("wuuf")

    ctrl = get_control_data(fname)
    pkg_rec = format_package_record(ctrl, fname)
    print(pkg_rec)
