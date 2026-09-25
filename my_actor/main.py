"""Module defines the main entry point for the Apify Actor.

Fetches real job postings from the Adzuna Jobs API based on the Actor's input
and pushes structured records to the dataset.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from apify import Actor

ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs/in/search/1"


async def main() -> None:
    """Define the main entry point for the Apify Actor."""
    async with Actor:
        actor_input = await Actor.get_input() or {}

        what = actor_input.get("what")
        if not what:
            msg = 'Missing "what" (job title / keywords) attribute in input!'
            raise ValueError(msg)

        where = actor_input.get("where", "")
        results_wanted = actor_input.get("results_wanted", 5)

        app_id = os.environ.get("ADZUNA_APP_ID")
        app_key = os.environ.get("ADZUNA_APP_KEY")
        if not app_id or not app_key:
            msg = "Missing ADZUNA_APP_ID / ADZUNA_APP_KEY environment variables!"
            raise ValueError(msg)

        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": str(results_wanted),
            "what": what,
            "content-type": "application/json",
        }
        if where:
            params["where"] = where

        url = f"{ADZUNA_BASE_URL}?{urllib.parse.urlencode(params)}"
        Actor.log.info(f"Searching Adzuna for what={what!r} where={where!r} ...")

        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

        results = data.get("results", [])
        Actor.log.info(f"Adzuna returned {len(results)} job(s).")

        for job in results:
            record = {
                "job_id": str(job.get("id", "")),
                "company": (job.get("company") or {}).get("display_name", ""),
                "title": job.get("title", ""),
                "location": (job.get("location") or {}).get("display_name", ""),
                "job_url": job.get("redirect_url", ""),
                "description": job.get("description", ""),
                "posted_date": job.get("created", ""),
                "source": "Adzuna",
            }
            await Actor.push_data(record)

        Actor.log.info("Pushed all job records into the dataset!")
