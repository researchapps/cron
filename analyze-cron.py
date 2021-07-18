#!/usr/bin/env python3

import logging
import tempfile
import fnmatch
import os
import re
from pathlib import Path
import json
import calendar
import time
import shutil
import yaml
import random
import json
from copy import deepcopy

from github import Github
from github.GithubException import UnknownObjectException, RateLimitExceededException
import git

from cron_descriptor import get_description
import pretty_cron

from dateutil.relativedelta import relativedelta
from croniter import croniter
from datetime import datetime, date

logging.basicConfig(level=logging.INFO)

# Calculate crons a year out
today = datetime.now()
in_two_months = today + relativedelta(months=2)

# We want the root
here = os.path.abspath(os.path.dirname(__file__))
data_dir = os.path.join(here, "data")

# do not clone LFS files
os.environ["GIT_LFS_SKIP_SMUDGE"] = "1"
g = Github(os.environ["GITHUB_TOKEN"])

core_rate_limit = g.get_rate_limit().core


def read_yaml(filename):
    with open(filename) as file:
        content = yaml.load(file, Loader=yaml.FullLoader)
    return content


def read_json(path):
    with open(path, "r") as fd:
        content = json.loads(fd.read())
    return content

def write_json(content, path):
    with open(path, 'w') as fd:
        fd.write(json.dumps(content, indent=4))


def rate_limit_wait():
    curr_timestamp = calendar.timegm(time.gmtime())
    reset_timestamp = calendar.timegm(core_rate_limit.reset.timetuple())
    # add 5 seconds to be sure the rate limit has been reset
    sleep_time = max(0, reset_timestamp - curr_timestamp) + 5
    logging.warning(f"Rate limit exceeded, waiting {sleep_time} seconds")
    time.sleep(sleep_time)


def call_rate_limit_aware(func):
    while True:
        try:
            return func()
        except RateLimitExceededException:
            rate_limit_wait()


def call_rate_limit_aware_decorator(func):
    def inner(*args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            except RateLimitExceededException:
                rate_limit_wait()

    return inner


@call_rate_limit_aware_decorator
def clone(repo, tmp, depth=1):
    """
    Rate limit aware clone
    """
    git.Repo.clone_from(repo.clone_url, tmp, depth=depth)


def main():
    """
    Entrypoint to run analysis
    """
    # Load previous crons
    data_path = os.path.join(data_dir, "crons.json")
    crons = read_json(data_path)

    # This search seems to return the best results! We search by indexed,
    # and it only returns top 10, and we can hope that over time we get closed
    # to the actual ~4k results (if we preserve older results).
    code_search = g.search_code('"cron:" path:.github/workflows language:YAML', sort="indexed", order=random.choice(["asc", "desc"]))
    total = code_search.totalCount
    print("Found %s results from code search" % total)

    # Lookup of repo, filename
    crons = {}

    for i, filename in enumerate(code_search):
        print(f"Parsing {i} of {total}")
        repo = filename.repository

        repo_name = repo.full_name
        if repo_name not in crons:
            crons[repo_name] = {}

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)

            # clone main branch
            try:
                clone(repo, str(tmp))
            except git.GitCommandError:
                continue

            # Look at filename
            try:
                filepath = os.path.join(tmp, filename.path)
                content = read_yaml(filepath)
             
                # Weird - for some reason the key "on" is read as True?
                key = True
                if key not in content and "on" in content:
                    key = "on"
                if "schedule" in content[key]:
                    crons[repo_name][filename.path] = content[key]['schedule']
            
            except:
                print("Issue reading %s" % filepath)

    # Save raw data
    write_json(crons, data_path)

    # For each cron create a description
    times = {}
    for repo, files in crons.items():
        for filename, cronlist in files.items():
            for entry in cronlist:
                if "cron" not in entry:
                    continue     

                description = pretty_cron.prettify_cron(entry['cron'])
                if not description.startswith("At"):
                    description = get_description(entry['cron'])
                print(description)

                if description not in times:
                    times[description] = 0
                times[description] +=1

    data_path = os.path.join(data_dir, "times.json")
    write_json(times, data_path)

    # now create custom descriptions
    every_times = {}
    for description, count in times.items():
        keys = ["day", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        for key in keys:
            key = "every %s" % key
            if key in description:
                if key not in every_times:
                    every_times[key] = 0
                every_times[key] += 1

    
    # TODO visualize on clock?
    # TODO how to visualize?
    # Save raw data
    data_path = os.path.join(data_dir, "every_times.json")
    write_json(times, data_path)


if __name__ == "__main__":
    main()
