#!/bin/bash -e

FULLNAME="$(python3 setup.py --fullname)"
echo "Press [ENTER] to deploy $FULLNAME to /shared"
read dummy

./clear_wheels.sh
python3 setup.py wheels bdist

DISTDIR=$(pwd)/dist
cd /shared/software/addons
rm -vf blender_cloud/wheels/*.whl  # remove obsolete wheel files
unzip $DISTDIR/$FULLNAME.addon.zip
