# National Reporter — Breaking News Bot

Runs every 2 hours on GitHub Actions. Checks The Hindu and NDTV feeds for Hyderabad, Telangana, India and World news, and publishes Hindi + Roman Urdu 3-bullet posts with the NR logo to the Facebook Page.

## Safety defaults
- **DRY_RUN=1 by default.** The bot only logs the posts it would publish. Set the repo variable `DRY_RUN` to `0` to go live.
- Posts only when **2+ preferred outlets** report the same event.
- Routine, opinion, entertainment, duplicate and already-posted stories are skipped.
- `data/posted.json` records every post so the same event is not published twice.

## One-time setup
1. Push this folder to your GitHub repo.
2. In **Settings → Secrets and variables → Actions → Secrets**, add:
   - `FB_PAGE_ID` — `239472476226069`
   - `FB_PAGE_TOKEN` — Page access token (`pages_manage_posts`, `pages_read_engagement`)
   - `GEMINI_API_KEY` — Google Gemini API key (from Google AI Studio)
3. In **Variables**, optionally add `DRY_RUN` = `1` (keep it as a test until you have reviewed a few runs).
4. Go to **Actions → Breaking news bot → Run workflow** to test.
5. Check the run log. When the dry-run output looks right, set `DRY_RUN` to `0`.

## Known limits
- Indian Express feeds currently block automated access, so only The Hindu and NDTV are used.
- Automatic classification can be wrong. Review the logs regularly.
- Page tokens can expire or be revoked. If publishing fails, the run fails and the log shows the Facebook error. Generate a new token and update `FB_PAGE_TOKEN`.
- Crime and security stories still follow the 2-source rule, but they have no extra human check. Turn on draft-then-post if you want a review step.
