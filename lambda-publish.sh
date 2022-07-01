#!/bin/bash

# This script will upload a new version of the lambda
rm code.zip
zip  code.zip s3apt.py config.py

(cd venv/lib/python3.9/site-packages/ ; zip -r ../../../../code.zip *)

aws lambda update-function-code --function-name s3apt_repo_maintainer --zip-file fileb://./code.zip --publish
