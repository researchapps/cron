#!/usr/bin/env python3

import logging
import tempfile
import os
import re
from pathlib import Path
import calendar
import time
import yaml
import random
import json
import operator
import sys

from github import Github
from github.GithubException import RateLimitExceededException
import git

from cron_descriptor import get_description
import pretty_cron

from dateutil.relativedelta import relativedelta
from croniter import croniter
from datetime import datetime

logging.basicConfig(level=logging.INFO)

# Calculate crons a year out
today = datetime.now()
in_two_months = today + relativedelta(months=2)

# We want the root
here = os.path.abspath(os.path.dirname(__file__))

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
    with open(path, "w") as fd:
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


def convert_to_frequency(seconds):
    seconds_in_week = 60 * 60 * 24 * 7
    seconds_in_day = 60 * 60 * 24
    seconds_in_hour = 60 * 60
    seconds_in_minute = 60

    weeks = seconds // seconds_in_week
    days = (seconds - (weeks * seconds_in_week)) // seconds_in_day
    hours = (
        seconds - (weeks * seconds_in_week) - (days * seconds_in_day)
    ) // seconds_in_hour
    minutes = (
        seconds
        - (weeks * seconds_in_week)
        - (days * seconds_in_day)
        - (hours * seconds_in_hour)
    ) // seconds_in_minute

    # Only include if not 0!
    result = ""
    if weeks:
        result += "%s weeks" % weeks
    if days:
        result += " %s days" % days
    if hours:
        result += " %s hours" % hours
    if minutes:
        result += " %s minutes" % minutes
    return result.strip()


@call_rate_limit_aware_decorator
def clone(repo, tmp, depth=1):
    """
    Rate limit aware clone
    """
    git.Repo.clone_from(repo.clone_url, tmp, depth=depth)


def do_code_search(string):
    """
    Do a code search for a query string
    """
    code_search = g.search_code(
        string, sort="indexed", order=random.choice(["asc", "desc"])
    )
    total = code_search.totalCount
    print("Found %s results from code search" % total)
    return code_search


def main():
    """
    Entrypoint to run analysis
    """
    # Run analysis for a username
    if len(sys.argv) == 2:
        run_username_analysis(sys.argv[1])
    else:
        run_analysis()


def download_repos(code_search):
    """
    Given code search results, download repos and parse cron

    The original results are stored by repository and filename so we can
    always add to them and not double count.
    """
    # Lookup of repo, filename
    crons = {}

    total = code_search.totalCount
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
                    crons[repo_name][filename.path] = content[key]["schedule"]

            except:
                print("Issue reading %s" % filepath)
    return crons


def calculate_frequencies(crons):
    """
    Given a lookup of crons (repos, files, and cron strings) derive frequencies
    """
    differences = {}
    for repo, files in crons.items():
        for filename, cronlist in files.items():
            for entry in cronlist:
                if "cron" not in entry:
                    continue
                # Doesn't allow ? (which means doesn't matter)
                entry["cron"] = entry["cron"].replace("?", "*")
                cron = croniter(entry["cron"])
                timestamp1 = int(cron.get_next())
                timestamp2 = int(cron.get_next())
                diff = timestamp2 - timestamp1

                # Start with seconds so we can sort!
                if diff not in differences:
                    differences[diff] = 0
                differences[diff] += 1

    # Sort by key (seconds) AND frequency
    by_freq = sorted(differences.items(), key=operator.itemgetter(0), reverse=True)
    by_count = sorted(differences.items(), key=operator.itemgetter(1), reverse=True)

    diffs = {"by_freq": {}, "by_count": {}}
    for diffset in by_freq:
        human_readable = convert_to_frequency(diffset[0])
        diffs["by_freq"][human_readable] = diffset[1]
    for diffset in by_count:
        human_readable = convert_to_frequency(diffset[0])
        diffs["by_count"][human_readable] = diffset[1]
    return diffs


def calculate_times_descriptions(crons):
    """
    Given the same lookup of crons, generate counts of times and descriptions
    """
    times = {}
    descriptions = {}

    for repo, files in crons.items():
        for filename, cronlist in files.items():
            for entry in cronlist:
                if "cron" not in entry:
                    continue

                # Doesn't allow ? (which means doesn't matter)
                description = pretty_cron.prettify_cron(entry["cron"])
                if not description.startswith("At"):
                    description = get_description(entry["cron"])

                if description not in descriptions:
                    descriptions[description] = 0
                descriptions[description] += 1

                # If we have a timestamp, capture it!
                match = re.search("[0-9]{2}:[0-9]{2}", description)
                if not match:
                    continue
                match = match.group()

                if match not in times:
                    times[match] = 0
                times[match] += 1

    # Sort from start to end
    ordered = sorted(times.items(), key=operator.itemgetter(0))
    descriptions = {
        k: v
        for k, v in sorted(descriptions.items(), reverse=True, key=lambda item: item[1])
    }
    return ordered, descriptions


def calculate_day_of_week(descriptions):
    """
    From descriptions, create counts based on day of week.
    """
    day_of_week = {}
    for description, count in descriptions.items():
        keys = [
            "day",
            "Sunday",
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
        ]
        for key in keys:
            key = "every %s" % key
            if key in description:
                if key not in day_of_week:
                    day_of_week[key] = 0
                day_of_week[key] += 1

    # Sort!
    day_of_week = {
        k: v
        for k, v in sorted(day_of_week.items(), reverse=True, key=lambda item: item[1])
    }
    return day_of_week


def run_username_analysis(username):
    """
    Run a cron analysis for a single user account
    """
    code_search = do_code_search(
        '"cron:" path:.github/workflows language:YAML user:%s' % username
    )
    crons = download_repos(code_search)

    data_dir = os.path.join(here, "data")
    data_dir = os.path.join(data_dir, username)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    run_common_analysis(crons, data_dir)


def run_common_analysis(crons, data_dir):
    """
    Shared functions to run an analysis
    """
    # First save
    write_json(crons, os.path.join(data_dir, "crons.json"))

    # Metric 1: frequency
    # Find difference between timepoints in seconds, take log scale
    diffs = calculate_frequencies(crons)
    write_json(diffs, os.path.join(data_dir, "frequencies.json"))

    # Given once a day, what time?
    # Or anything with a time
    times, descriptions = calculate_times_descriptions(crons)
    write_json(times, os.path.join(data_dir, "times.json"))
    write_json(descriptions, os.path.join(data_dir, "descriptions.json"))

    # Day of week (or every day)
    day_of_week = calculate_day_of_week(descriptions)
    write_json(day_of_week, os.path.join(data_dir, "day_of_week.json"))


def run_analysis():

    # Load previous crons so we acculumate over time
    data_dir = os.path.join(here, "data")
    data_path = os.path.join(data_dir, "crons.json")
    crons = read_json(data_path)
    code_search = do_code_search('"cron:" path:.github/workflows language:YAML')

    # Update crons with new entries
    crons.update(download_repos(code_search))
    run_common_analysis(crons, data_dir)


if __name__ == "__main__":
    main()
