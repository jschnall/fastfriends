#!/bin/bash
# Util for committing and pushing changes to heroku and github all at once  

USAGE="usage: push <commit message>"
if [[ "$#" < 1 ]]; then
    echo "$USAGE" 
    exit
fi

msg="$1"
if [[ $msg = "" ]]; then
    echo "Commit message cannot be empty."
    echo $USAGE
else
    #echo $msg
    git add --all
    git commit -m msg
    git push origin master
    git push staging master
fi
