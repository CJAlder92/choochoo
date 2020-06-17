#!/bin/bash

# 'experimental' and DOCKER_BUILDKIT is related to the pip cache
# https://stackoverflow.com/a/57282479

VERSION=`grep 'CH2_VERSION =' ../py/ch2/commands/args.py | sed -e "s/.*CH2_VERSION *= *'\([0-9]\+\.[0-9]\+\)\.[0-9]\+'.*/\1/"`
VERSION=`echo $VERSION | sed -e s/\\\\./-/g`
CMD=$0

BASE=python:3.8.3-slim-buster
COMMENT="# syntax=docker/dockerfile:experimental"
MOUNT="--mount=type=cache,target=/root/.cache/pip"
JS_PKG="npm"
HAVE_JS=0
URI="--sqlite"
FILE="Dockerfile"

help () {
    echo -e "\n  Create the dev image used to run Choochoo in Docker"
    echo -e "\n  Usage:"
    echo -e "\n    $CMD [--big] [--slow] [--dev] [--pg] [-h] [FILE]"
    echo -e "\n    FILE:      destination file name (default Dockerfile)"
    echo -e "  --big:       use larger base distro"
    echo -e "  --slow:      do not mount pip cache (buildkit)"
    echo -e "  --dev:       dev work (assumes node pre-built)"
    echo -e "  --pg:        assume a postgres database on host pg"
    echo -e "  -h:          show this message\n"
    exit 1
}

while [ $# -gt 0 ]; do
    if [ $1 == "-h" ]; then
	help
    elif [ $1 == "--big" ]; then
	BASE=python:3.8.3-slim-buster
    elif [ $1 == "--slow" ]; then
	COMMENT="# pip cache disabled with --no-cache"
	MOUNT=
    elif [ $1 == "--dev" ]; then
	HAVE_JS=1
	JS_PKG=
    elif [ $1 == "--pg" ]; then
	URI="--uri postgresql://postgres@pg/activity-$VERSION"
    else
	echo -e "\nERROR: do not understand $1\n"
	help
    fi
    shift
done

pushd .. > /dev/null
source py/env/bin/activate

pip freeze > dkr/requirements.txt

# basic image and support
# (we need to install db libs whatever db we are using because of python deps)
cat > $FILE <<EOF
$COMMENT
from $BASE
workdir /tmp
run apt-get update
run apt-get -y install sqlite3 libsqlite3-dev libpq-dev $JS_PKG gcc emacs
EOF

# python libs that are needed in all cases
cat >> $FILE <<EOF
copy dkr/requirements.txt /tmp
run $MOUNT \\
    pip install --upgrade pip && \\
    pip install wheel && \\
    pip install -r requirements.txt
copy py/ch2 /app/py/ch2
copy py/setup.py py/MANIFEST.in /app/py/
EOF

if (( HAVE_JS )); then
    # if we're in a dev enrironment the js will have been built locally
    if [ ! -f py/ch2/web/static/bundle.js.gz ]; then
	echo -e "\nERROR: missing bundle.js.gz"
	exit 2
    fi
else
    # otherwise we need to do it all in the docker build :(
    cat >> $FILE <<EOF
copy js/package.json js/package-lock.json js/webpack.config.js /app/js/
workdir /app/js
run npm install -g npm@next
run npm install
# do this after install so that we use a separate layer
copy js/src /app/js/
run npm build
EOF
fi

# python install of ch2 package
cat >> $FILE <<EOF
workdir /app/py
run pip install .
EOF

if (( HAVE_JS )); then
    # if we're in a dev enrironment the profile will have been built locally
    if [ ! -f py/ch2/fit/profile/global-profile.pkl ]; then
	echo -e "\nERROR: missing global-profile.pkl"
	exit 3
    fi
else
    # otherwise, package with ch2
    cat >> $FILE <<EOF
workdir /app
copy data/sdk/Profile.xlsx /app
run ch2 package-fit-profile ./Profile.xlsx
EOF
fi

# finally, start up
cat >> $FILE <<EOF
workdir /
expose 8000 8001
copy dkr/docker-start.sh .
cmd ./docker-start.sh
EOF

echo -e "\ncreated $FILE for $VERSION ($URI)\n"
