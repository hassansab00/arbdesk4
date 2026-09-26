# Put a GitHub token in Supabase (5 minutes)

**Why:** the n8n "clock" workflow uses about 24 of your n8n executions a day
only to start the hourly GitHub job. With a GitHub token stored in Supabase,
Supabase starts that job itself and those n8n executions go to zero.

You only do this once. Never paste the token into chat.

## Part 1 - make the token on GitHub

1. Go to **https://github.com/settings/personal-access-tokens/new**
   (or: your profile picture, top right -> **Settings** -> scroll to the
   bottom of the left menu -> **Developer settings** -> **Personal access
   tokens** -> **Fine-grained tokens** -> **Generate new token**).
2. **Token name:** `AD4 Supabase clock`
3. **Expiration:** choose **Custom** -> one year from today.
4. **Resource owner:** `hassansab00`
5. **Repository access:** click **Only select repositories** -> choose
   **arbdesk4**.
6. **Permissions** -> **Repository permissions** -> find **Actions** -> set
   it to **Read and write**. Leave everything else as it is
   (Metadata: Read-only is added automatically).
7. Click **Generate token** at the bottom.
8. Click the copy icon next to the token (it starts with `github_pat_`).
   GitHub shows it only once.

## Part 2 - store it in Supabase

1. Go to **https://supabase.com/dashboard/project/jittmxhzgqpifitwupss/sql/new**
   (or: Supabase -> your project -> **SQL Editor** in the left menu ->
   **New query**).
2. Paste this, then replace `PASTE_TOKEN_HERE` with the token (keep the
   single quotes around it):

   ```sql
   select vault.create_secret('PASTE_TOKEN_HERE', 'github_actions_token', 'clock: dispatches GitHub workflows');
   ```

3. Click **Run** (bottom right). It answers with one row holding a long id.
   That means it worked.
4. Close the tab. Don't save the query (it contains the token).

## Part 3 - tell Claude

Reply **"token done"**. Claude checks that it exists (without reading it),
switches the hourly clock from n8n to Supabase, and turns off the n8n clock's
schedule.

If anything on these screens looks different, send a screenshot of the page
**without** the token visible.
