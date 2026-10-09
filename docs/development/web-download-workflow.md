# Web download workflow fixes

This branch fixes Spotify URL handling and the lifetime of downloads started from the web interface. It does not add the experimental folder picker or progress panel used in a private deployment.

## Behavior

- Public Spotify URLs containing a locale segment, such as `/intl-es/track/...`, are normalized before web validation and query parsing. Query strings and fragments are preserved. Non-Spotify inputs are left unchanged.
- The web downloader uses `parse_query`, the same parser used by the command-line workflow, to resolve individual tracks, playlists, albums and artists. Track and playlist execution are covered by regression tests; live album and artist downloads have not been validated in this work.
- A download task is registered with its client before returning an SSE redirect. Navigation or cancellation of the HTTP request does not cancel the task.
- Each client has a lock: multiple submitted queues for that client run sequentially. Separate clients have separate locks.
- Individual song failures are logged and the remaining songs continue.
- The total number of songs accumulates across queues so the existing completed counter has a corresponding total.
- Inactive clients are retained while they have unfinished tasks. Session cleanup timers run on the asyncio loop. Reconnection cancels the cleanup timer.
- Server shutdown cancels and awaits background coroutines and cancels client cleanup timers before session-directory removal.

Downloads are in-memory tasks, not durable jobs. A server restart does not resume a queue. Cancelling a coroutine does not guarantee immediate termination of synchronous work already running through `asyncio.to_thread`; network metadata work may finish later. Shutdown tests cover coroutine ordering, not termination of every external downloader subprocess.

Repeated submissions are separate queues; URL deduplication and queue limits are not introduced here. Settings are read when a queued download starts, not captured at submission. Existing download progress views remain in use; additional progress UI and destination selection are separate work.

## Implementation

| File                                     | Responsibility                                                                                  |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `spotdl/utils/web.py`                    | URL normalization, client-owned task references, per-client lock and session cleanup            |
| `spotdl/web/routes.py`                   | Background task launch before redirect, query parsing and list processing                       |
| `spotdl/web/api.py`                      | Shutdown cancellation before session cleanup                                                    |
| `tests/test_web_spotify_urls.py`         | Locale normalization and unchanged inputs                                                       |
| `tests/test_web_playlist_downloads.py`   | Single/list downloads, accumulated totals and continuation on failure                           |
| `tests/test_web_background_downloads.py` | Request cancellation, task errors, serialized queues, session retention, reconnect and shutdown |

Completed tasks are removed from the client's task set. Their exceptions are retrieved and logged to avoid unobserved task failures. The URL route returns after enqueueing and does not subsequently perform a text search.

## Reproducible checks

From the repository root:

```sh
uv sync --frozen
uv run pytest tests/test_web_spotify_urls.py tests/test_web_playlist_downloads.py tests/test_web_background_downloads.py -q
uv run pylint --fail-under 10 --limit-inference-results 0 --disable=R0917 ./spotdl
uv run mypy --ignore-missing-imports --follow-imports silent ./spotdl
uv run black --check ./spotdl
uv run isort --check --diff ./spotdl
git diff --check
```

For broader tests, use the exclusions from the existing non-recording CI job in `.github/workflows/tests.yml`. That job does not cover the entire repository suite or the live matching job. Do not change dependency pins merely to reproduce these web fixes.

## Manual verification

Use a writable test output directory and a public playlist containing at least two tracks:

```sh
uv run spotdl web --host 127.0.0.1 --port 8801 --web-use-output-dir --threads 1 --output "./web-test/{artist}/{album}/{title}.{output-ext}"
```

Submit the playlist, navigate to Downloads or refresh after execution has started, and verify files on disk. A queue entry alone is not evidence that a file was saved. Also submit two lists in the same client and check sequential execution, then reconnect while the client has a pending queue.

During user-run Windows verification, the playlist resolved two tracks and an MP3 was produced for the second after the first encountered a provider error. This verifies continuation after failure, not two successful audio downloads. Those manual runs preceded the final lifecycle review; the final changes are covered by automated lifecycle tests.

The locked yt-dlp version produced a YouTube HTTP 403 in a direct download test outside spotDL. A local environment update was used for subsequent manual work; no lockfile or dependency changes are included in this branch. Intermittent Spotify metadata requests also failed when fetching Spotify JavaScript and succeeded in a fresh-process check. Neither external-service failure is fixed here.

## Validation record

On 2026-10-09, in Linux with Python 3.12.14 and the frozen repository dependencies:

- Focused regression suite: **35 passed**, with an existing Starlette multipart deprecation warning.
- Mypy: **no issues in 60 source files**.
- Pylint: **10.00/10** for the entire `spotdl` package with CI options.
- Black and isort checks: passed for the entire package.
- Whitespace check: passed.

Black reports that the interpreter is older than the inferred Python 3.14 target, but the package formatting check passes. Changed code was formatted with the project's supported minimum target, Python 3.10.

## Contribution policy

The upstream `docs/CONTRIBUTING.md` explicitly prohibits AI-generated code and AI-generated issue or pull-request text. These follow-up changes and this document were prepared with AI assistance for the user's fork. They should not be submitted upstream as a compliant human-authored contribution. The earlier draft PR attempt through the connector was rejected with HTTP 403; it did not create a PR.

### Broader CI-style test comparison

The non-recording CI-style command completed with **96 passed and 23 failed** on this branch. The identical command on untouched upstream commit `cd4a4203`, in the same environment, completed with **61 passed and the same 23 failed tests**. The difference is the 35 added regression cases. The broader suite is therefore **not fully passing**, but its observed failures also reproduce in the baseline.

The failed cases are in album/artist/playlist/song metadata, M3U and search tests (Spotify/YouTube-dependent requests), and five configuration tests reporting `PosixPath` type errors. The baseline comparison does not demonstrate that live service access works or that all CI platforms pass.

Command used on both trees:

```sh
python -m pytest -q --record-mode=none --disable-recording \
  --ignore tests/providers/lyrics \
  --ignore tests/utils/test_github.py \
  --ignore tests/utils/test_ffmpeg.py \
  --ignore tests/utils/test_metadata.py \
  --ignore tests/test_matching.py \
  --ignore tests/providers/audio/test_youtube.py \
  --ignore tests/console/test_entry_point.py \
  --ignore tests/test_init.py
```

## Updating a local checkout

After the follow-up commit is published to the fork, update an existing clean checkout with:

```sh
git fetch fork
git switch fix/web-download-workflow
git merge --ff-only fork/fix/web-download-workflow
```

This updates the contribution checkout only. Existing Docker images and the Raspberry deployment are not automatically rebuilt. Keep the previously working image available when testing a replacement.
