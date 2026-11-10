# Putting Tally Ho! Online

These steps put Tally Ho! on the internet for free: the website on **Render**, the data on **Neon**. Do them in order. Each one says where to click.

You need: this GitHub repo, a Neon account and a Render account. All are free.

## 1. The database (Neon)

1. Go to [neon.tech](https://neon.tech) and log in.
2. Press **New Project**. Name: `tally-ho`. Region: **Asia Pacific (Singapore)**. Press **Create**.
3. On the project page, press **Connect**. Copy the **connection string**. It starts with `postgresql://`.
4. Keep it secret: it has the database password in it. Never put it in a file in the repo or in a chat.

Neon's free plan has 0.5 GB. One demo is about 10,000 orders; at most 20 demos live at once, and each is deleted after 2 hours. **Don't** run the million-line benchmark against Neon: run it on your computer (it uses SQLite).

## 2. Check the tests on PostgreSQL (optional, but good)

The tests have only run on SQLite so far. Make a separate, empty Neon branch for them:

1. In Neon, open **Branches**, press **New Branch**, name it `test`. Copy its connection string.
2. In PowerShell, start in the project folder, then run these one line at a time:

```
cd C:\Users\USER\Documents\Workspace\analytics_dashboard
$env:TEST_DATABASE_URL = "postgresql://...the test branch's string..."
.venv\Scripts\python -m pytest
Remove-Item Env:TEST_DATABASE_URL
```

It is slow over the internet (maybe 20 minutes). If your connection drops, run `.venv\Scripts\python -m pytest --lf` to run only the ones that didn't finish.

## 3. Make the tables and your login (from your computer)

Render's free plan has no command line, so do this once from your own computer, with the **main** branch's string. One line at a time:

```
cd C:\Users\USER\Documents\Workspace\analytics_dashboard
$env:DATABASE_URL = "postgresql://...the main branch's string..."
.venv\Scripts\flask --app tallyho upgrade-db
.venv\Scripts\flask --app tallyho create-owner --business "Your Business"
Remove-Item Env:DATABASE_URL
```

`create-owner` asks for your name, email and a password.

## 4. The website (Render)

1. Go to [render.com](https://render.com) and log in with GitHub.
2. Press **New**, then **Blueprint**. Choose the `analytics_dashboard` repo.
3. Render reads `render.yaml` and asks for the values it doesn't know:
   - `DATABASE_URL`: the Neon **main** branch's connection string.
   - `BASE_URL`: leave empty for now; after the first deploy, set it to the address Render shows.
   - `MAIL_USERNAME` and `MAIL_PASSWORD`: leave empty for now (step 6).
4. Press **Apply**. The first deploy takes a few minutes. Then open the address Render shows (like `https://tally-ho.onrender.com`).
5. Log in with your email and password, or press **Try the demo**. The first demo takes a few seconds longer: it makes the sample sales once.

The free plan sleeps after 15 quiet minutes. The next visit wakes it up in about 30 to 50 seconds.

## 5. The timer (GitHub Actions)

The weekly email, the alert checks and the clean-up of old demos run every hour from GitHub:

1. On Render, open the service, then **Environment**. Copy the value of `TICK_KEY`.
2. On GitHub, open the repo's **Settings**, then **Secrets and variables**, then **Actions**. Add two secrets:
   - `TICK_URL`: `https://<your Render address>/tasks/tick`
   - `TICK_KEY`: the value you copied.
3. Open **Actions**, choose **Timer**, and press **Run workflow** once to try it. It should end with a short JSON line like `{"demos_deleted": 0, "summaries": 0, "alerts": 0}`.

## 6. The weekly email (Gmail, optional)

1. Use a Gmail account. In **Google Account**, open **Security** and turn on **2-Step Verification**.
2. Search the account settings for **App passwords**. Make one named `Tally Ho`. Copy the 16 letters.
3. On Render, set `MAIL_USERNAME` (the Gmail address) and `MAIL_PASSWORD` (the 16 letters). Save.
4. On the site, open **Settings** and tick **Email me a summary every Monday**.

## 7. Your sales

Open **Import** and upload a CSV or Excel file of your orders (one row per line of an order). The page guesses which column is which; check it and press **Import**. Files up to 20 MB go through the page. For a bigger file, from your computer:

```
cd C:\Users\USER\Documents\Workspace\analytics_dashboard
$env:DATABASE_URL = "postgresql://...the main branch's string..."
.venv\Scripts\flask --app tallyho import-sales "C:\path\to\big-file.csv"
Remove-Item Env:DATABASE_URL
```

## After a change

Every merge into `main` deploys by itself. Once, a deploy didn't start after a merge (on another project): if the site doesn't change, open the service on Render and press **Manual Deploy**.
