#!/bin/sh
set -e

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <tag>"
    exit 1
fi

TAG="$1"
IMAGE_REPO="registry.rileymathews.com/rileymathews/davhome"
IMAGE_REF="${IMAGE_REPO}:${TAG}"

docker build -t "$IMAGE_REF" .
docker push "$IMAGE_REF"
