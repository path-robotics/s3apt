
s3apt
=====

Host Private Ubuntu Repos in S3

This code watches S3 buckets for changes, and rebuilds the debian package index
whenever something changes.  It is the cloud equivalent of *dpkg-scanpackages*.

There are 2 parts to this: Lambda configuration and apt-get configuration

Lambda Quick Start
------------------

To get started, you need to configure your bucket name and upload the code as a
lambda.

Clone the repo.

```
git clone https://github.com/szinck/s3apt.git
cd s3apt
```

Install prerequisite software.

```
virtualenv venv
. venv/bin/activate
pip install -r requirements.txt
```

Configure your bucket name.

```
cp config.py.example config.py
vim config.py
# Edit the APT_REPO_BUCKET_NAME to be the name of the bucket (with no s3:// prefix)
```

Create a zip file of the code.

```
zip  code.zip s3apt.py config.py
(cd venv/lib/python2.7/site-packages/ ; zip -r ../../../../code.zip *)
```

Create a new lambda in the AWS Console, and upload the zip file.

Set the lambda handler as **s3apt.lambda_handler** and configure triggers as
below.

* Object Created (ALL), prefix=/dists/
* Object Removed (ALL), prefix=/dists/

Start uploading files to S3, and the lambda should keep everything in sync.

Apt-get configuration
---------------------

For details on apt-get configuration see
http://webscale.plumbing/managing-apt-repos-in-s3-using-lambda
