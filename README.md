s3apt
=====

Host Private Ubuntu Repos in S3

[Confluence Documentation](https://pathrobotics.atlassian.net/wiki/spaces/DEV/pages/117702763/S3+Apt+Repository)

This code watches S3 buckets for changes, and rebuilds the debian package index
whenever something changes.  It is the cloud equivalent of *dpkg-scanpackages*.

There are 2 parts to this: Lambda configuration and apt-get configuration

Prerequisites
-------------
You need the apt transport driver in order to use `apt` to pull packages from the s3.
```bash
sudo apt install apt-transport-s3
```
Configure aws credentials for the apt-transport at `/etc/apt/s3auth.conf`. Replace `myaccesskey` and `mysecretaccesskey` with real values. Access keys can be created by logging into AWS, clicking your User in the top right, selecting "My Security Credentials" then clicking "Create Access Key". If you’ve setup the benchmark part library or the aws-cli you may already have a key pair at `~/.aws/credentials`.
```bash
AccessKeyId=myaccesskey
SecretAccessKey=mysecretaccesskey
Region=us-east-2
Token=''
```
Add the apt repository to `sources.list`:
```bash
echo 'deb [trusted=yes lang=en arch=amd64] s3://path-apt-repo bionic/' | sudo tee /etc/apt/sources.list.d/path-robotics-software.list
```
Update your local package index:
```bash
apt update
```

Lambda Quick Start
------------------

To get started, you need to configure your bucket name and upload the code as a
lambda.

Clone and setup the repo.

```bash
git clone https://github.com/szinck/s3apt.git
cd s3apt
virtualenv venv
. venv/bin/activate
pip install -r requirements.txt
```

Configure your bucket name.

```bash
cp config.py.example config.py
vim config.py
# Edit the APT_REPO_BUCKET_NAME to be the name of the bucket (with no s3:// prefix)
```

Example:
```bash
APT_REPO_BUCKET_NAME = "my-bucket"
CONTROL_DATA_CACHE_PREFIX = "control-data-cache"
```

Create a zip file of the code.

```bash
zip code.zip s3apt.py config.py
(cd venv/lib/python3.6/site-packages/ ; zip -r ../../../../code.zip *)
```

Create a new lambda in the AWS Console, and upload the zip file.

Increase the timeout from the default of 3 seconds ( Configuration -> General Configuration -> Timeout) to something longer like 10 minutes (to allow the code time to re-index all the packages). 

Set the lambda handler as **s3apt.lambda_handler** and configure triggers as
below.  Note, there should be no leading slash before the name of the prefix.

* Object Created (ALL), prefix=dist/
* Object Removed (ALL), prefix=dist/

Example of Object Removed:
```bash
Bucket: s3/my-bucket
Event type: ObjectRemoved
Notification name: c45f5e77-ce4b-40ff-8548-066130f9c495
Prefix: bionic/
```

Start uploading files to S3, and the lambda should keep everything in sync.

Apt-get configuration
---------------------

For details on apt-get configuration see the [Confluence Page](https://pathrobotics.atlassian.net/wiki/spaces/DEV/pages/117702763/S3+Apt+Repository) or 
http://webscale.plumbing/managing-apt-repos-in-s3-using-lambda

Adding Installers to the Repository
-----------------------------------

Configure your `buildspec.yaml` to upload the generated artifact (.deb installer) to the bionic directory.

Packages are pushed using the AWS CLI. 
```bash
aws s3 cp ${deb} ${INSTALLER_BUCKET_URL}/${deb}
ie.
aws s3 cp my-package.deb s3://path-apt-repo/bionic
```
The lambda will be automatically triggered to regenerate the Packages index.


Testing
-------

Setup

```
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

Testing against your packages:

If you specify the name of the package on the command line you can cause the
code to generate a package index entry.

```bash
$ python s3apt.py elasticsearch-2.3.3.deb

Package: elasticsearch
Version: 2.3.3
Section: web
Priority: optional
Architecture: all
Depends: libc6, adduser
Installed-Size: 30062
Maintainer: Elasticsearch Team <info@elastic.co>
Description: Elasticsearch is a distributed RESTful search engine built for the cloud. Reference documentation can be found at https://www.elastic.co/guide/en/elasticsearch/reference/current/index.html and the 'Elasticsearch: The Definitive Guide' book can be found at https://www.elastic.co/guide/en/elasticsearch/guide/current/index.html
Homepage: https://www.elastic.co/
Size: 27426588
MD5sum: e343866c166ca1ef69b9115d36deeae2
SHA1: 8385dda7aa3870a3b13fc1df91d8b750d940700e
SHA256: fa90c6aefc5e82e0e19cb0ec546b9a64fec354ede201cf24658ddcfe01762d92
```

Fixing
------
If the `Packages` file becomes corrupted by improperly built packages you can fix it by doing the following:
- Identify the offending package by downloading the `Packages` file from s3
```bash
aws s3 cp s3://my-bucket/bionic/Packages Packages
```
- Remove the offending package from the S3.  Because the bucket is versioned this will require you to pass the broken version ID for the object you wish to delete.  You can find the version number under the Versions tab of the object details page.
```bash
aws s3api delete-object --bucket <bucket_name> --key <distro>/<object_name> --version-id <version_id>
aws s3api delete-object --bucket my-bucket --key bionic/global_sensor_optimization-4.0.0-Linux.deb --version-id QqcIbcm3RFCdUfqcLz9xNw61LOXd_7cr
```
- Delete the control-data-cache entry for the offending package.  This will match the MD5sum value from the offending package entry in the Packages file.
- Delete the broken version of the Packages file from S3.
```bash
aws s3api delete-object --bucket my-bucket --key bionic/Packages --version-id QqcIbcm3RFCdUfqcLz9xNw61LOXd_7cr
```
- Rebuild the package index by sending the following JSON in the `Test` tab on the Lambda page.
```json
{
  "action": "rebuild_package_index",
  "prefix": "bionic/",
  "Records": [
    {
      "s3": {
        "bucket": {
          "name": "my-bucket"
        }
      }
    }
  ]
}
```

If this operation fails there may still be a broken package.  Check the `Packages` file again.  Also make sure the offending package and data cache entry have been removed.