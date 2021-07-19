# Cron

When do we run GitHub cron jobs?

![img/cron.png](img/cron.png)

⭐️ [Here is what I found](https://researchapps.github.io/cron/) ⭐️


## Background

I tend to be consistent with when I run cron jobs, typically choosing the middle
of the night, or the first few days of the month. It occurred to me that this might
be true of others, and it would be interesting to see when users are running
cron jobs, as evidenced by their github workflow files.

I can only sample a subset of search results, so this analysis should be considered
just a sample.

## Usage

First, install requirements (ideally in a virtual environment).

```bash
$ pip install -r requirements.txt
```

Export a GitHub personal access token to the environment:

```bash
$ export GITHUB_TOKEN=xxxxxxxxxxx
```

And run the script to generate the data:

```bash
$ python analyze-cron.py
```

Without a username (above) will do a general GitHub search with up to 1K results.
You can also a username to search for specific orgs/users:

```bash
$ python analyze-cron.py vsoch
```

This will generate data in [data](data) that renders into [index.html](index.html).
Example data is provided here, along with generating for [my username](data/vsoch).
There are over 400K results, but we can only get 1000, so it's just a small sample.
If you run this again, you will likely get different results as it's based on indexing.
