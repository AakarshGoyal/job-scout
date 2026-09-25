"""Module defines the main entry point for the Apify Actor.

Fetches real job postings from the Adzuna Jobs API based on the Actor's
input. Adzuna's search API only returns a short, truncated snippet of each
job description, so this Actor enriches every result with the FULL
description by crawling Adzuna's own details page for that job
(https://www.adzuna.in/details/<job_id>) using the apify/website-content-crawler
Actor, which handles JavaScript rendering and anti-bot protection for us.

If the enrichment step fails for any reason (network issue, crawler error),
the Actor falls back to Adzuna's short snippet for that job rather than
failing the whole run.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from apify import Actor

ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs/in/search/1"
ADZUNA_DETAILS_URL = "https://www.adzuna.in/details/{job_id}"
CONTENT_CRAWLER_ACTOR = "apify/website-content-crawler"


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

        # Build the base records from Adzuna. "description" here is Adzuna's
        # short, truncated snippet -- used only as a fallback if the full
        # description can't be fetched below.
        jobs = []
        for job in results:
            job_id = str(job.get("id", ""))
            jobs.append(
                {
                    "job_id": job_id,
                    "company": (job.get("company") or {}).get("display_name", ""),
                    "title": job.get("title", ""),
                    "location": (job.get("location") or {}).get("display_name", ""),
                    "job_url": job.get("redirect_url", ""),
                    "description": job.get("description", ""),
                    "posted_date": job.get("created", ""),
                    "source": "Adzuna",
                }
            )

        # Enrich with full descriptions by crawling each job's Adzuna details
        # page, since the search API only gives us a short snippet.
        detail_urls = {
            j["job_id"]: ADZUNA_DETAILS_URL.format(job_id=j["job_id"])
            for j in jobs
            if j["job_id"]
        }

        if detail_urls:
            Actor.log.info(
                f"Fetching full descriptions for {len(detail_urls)} job(s) "
                f"via {CONTENT_CRAWLER_ACTOR} ..."
            )
            try:
                actor_run = await Actor.call(
                    actor_id=CONTENT_CRAWLER_ACTOR,
                    run_input={
                        "startUrls": [{"url": u} for u in detail_urls.values()],
                        "crawlerType": "playwright:adaptive",
                        "maxCrawlDepth": 0,
                        "maxCrawlPages": len(detail_urls),
                        "saveMarkdown": True,
                        "proxyConfiguration": {"useApifyProxy": True},
                    },
                )

                if actor_run is None:
                    Actor.log.warning(
                        "Content crawler did not start; keeping short descriptions."
                    )
                else:
                    run_client = Actor.apify_client.run(actor_run.id)
                    dataset_client = run_client.dataset()
                    item_list = await dataset_client.list_items()

                    # Map each crawled page back to the job it belongs to via its URL.
                    url_to_markdown = {
                        item.get("url", ""): item.get("markdown", "")
                        for item in item_list.items
                    }

                    filled = 0
                    for j in jobs:
                        detail_url = detail_urls.get(j["job_id"])
                        full_text = url_to_markdown.get(detail_url)
                        if full_text:
                            j["description"] = full_text
                            filled += 1

                    Actor.log.info(
                        f"Filled in full descriptions for {filled}/{len(jobs)} job(s)."
                    )
            except Exception as exc:  # noqa: BLE001 - never fail the whole run over this
                Actor.log.warning(
                    f"Full-description crawl failed ({exc}); keeping short descriptions "
                    "for this run."
                )

        for j in jobs:
            await Actor.push_data(j)

        Actor.log.info("Pushed all job records into the dataset!")
