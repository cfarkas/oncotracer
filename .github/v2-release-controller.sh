#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

CANDIDATE_BRANCH="agent/v2.0-release-validation-2"
PRODUCT_BRANCH="agent/v2.0-release-candidate"
CANDIDATE_SHA="2af7974a73fda80e866ae0d06a3056b01fb1b38d"
PRODUCT_PR="31"
VALIDATION_PR="33"

comment_validation() {
  gh pr comment "$VALIDATION_PR" --repo "$GITHUB_REPOSITORY" --body "$1"
}

branch_sha() {
  local branch="$1"
  gh api "/repos/$GITHUB_REPOSITORY/git/ref/heads/$branch" --jq '.object.sha'
}

resolve_run() {
  local workflow="$1" branch="$2" expected_sha="$3" start_time="$4" run_id=""
  local attempt
  for attempt in $(seq 1 180); do
    run_id="$(gh api \
      "/repos/$GITHUB_REPOSITORY/actions/workflows/$workflow/runs?event=workflow_dispatch&branch=$branch&per_page=50" \
      --jq ".workflow_runs | map(select(.head_sha == \"$expected_sha\" and .created_at >= \"$start_time\")) | sort_by(.id) | last | .id // empty")"
    if [[ -n "$run_id" ]]; then
      printf '%s\n' "$run_id"
      return 0
    fi
    sleep 5
  done
  echo "Timed out resolving $workflow for $branch@$expected_sha" >&2
  return 1
}

dispatch_gate_set() {
  local branch="$1" expected_sha="$2" label="$3"
  local start_time workflow run_id index conclusion current status=0
  local workflows=(
    native-v2-ci.yml
    native-v2-quickstart1-parity.yml
    native-v2-quickstart2-parity.yml
  )
  local run_ids=()

  test "$(branch_sha "$branch")" = "$expected_sha"
  start_time="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for workflow in "${workflows[@]}"; do
    gh workflow run "$workflow" --ref "$branch" --repo "$GITHUB_REPOSITORY"
  done

  local dispatched="Dispatched $label validation for \`$expected_sha\`:"
  for workflow in "${workflows[@]}"; do
    run_id="$(resolve_run "$workflow" "$branch" "$expected_sha" "$start_time")"
    run_ids+=("$run_id")
    dispatched="$dispatched
- \`$workflow\`: https://github.com/$GITHUB_REPOSITORY/actions/runs/$run_id"
  done
  comment_validation "$dispatched"

  local summary="Completed $label validation for \`$expected_sha\`:"
  for index in "${!workflows[@]}"; do
    workflow="${workflows[$index]}"
    run_id="${run_ids[$index]}"
    if gh run watch "$run_id" --exit-status --repo "$GITHUB_REPOSITORY"; then
      conclusion=success
    else
      conclusion=failure
      status=1
    fi
    summary="$summary
- \`$workflow\`: **$conclusion** — https://github.com/$GITHUB_REPOSITORY/actions/runs/$run_id"
  done
  current="$(branch_sha "$branch")"
  if [[ "$current" != "$expected_sha" ]]; then
    summary="$summary
- branch moved to \`$current\`: **failure**"
    status=1
  fi
  comment_validation "$summary"
  return "$status"
}

if gh release view v2.0.0 --repo "$GITHUB_REPOSITORY" \
    --json tagName,isDraft,isPrerelease >/dev/null 2>&1; then
  echo 'v2.0.0 already exists; refusing duplicate release orchestration.'
  exit 0
fi

test "$(branch_sha "$CANDIDATE_BRANCH")" = "$CANDIDATE_SHA"
test "$(branch_sha "$PRODUCT_BRANCH")" = "$CANDIDATE_SHA"
test "$(gh api "/repos/$GITHUB_REPOSITORY/pulls/$PRODUCT_PR" --jq '.head.sha')" = "$CANDIDATE_SHA"
PR_STATE="$(gh api "/repos/$GITHUB_REPOSITORY/pulls/$PRODUCT_PR" --jq '.state')"
PR_MERGED="$(gh api "/repos/$GITHUB_REPOSITORY/pulls/$PRODUCT_PR" --jq '.merged')"
if [[ "$PR_STATE" != open && "$PR_MERGED" != true ]]; then
  echo "Product PR is neither open nor merged" >&2
  exit 1
fi
comment_validation "<!-- v2-candidate-validation-running:$CANDIDATE_SHA -->
Started exact-head candidate validation for \`$CANDIDATE_SHA\`."
dispatch_gate_set "$CANDIDATE_BRANCH" "$CANDIDATE_SHA" 'candidate'
comment_validation "<!-- v2-candidate-validation-complete:$CANDIDATE_SHA -->
All three exact-head candidate gates passed for \`$CANDIDATE_SHA\`."

test "$(branch_sha "$PRODUCT_BRANCH")" = "$CANDIDATE_SHA"
test "$(gh api "/repos/$GITHUB_REPOSITORY/pulls/$PRODUCT_PR" --jq '.head.sha')" = "$CANDIDATE_SHA"
PR_STATE="$(gh api "/repos/$GITHUB_REPOSITORY/pulls/$PRODUCT_PR" --jq '.state')"
PR_MERGED="$(gh api "/repos/$GITHUB_REPOSITORY/pulls/$PRODUCT_PR" --jq '.merged')"
if [[ "$PR_STATE" != open && "$PR_MERGED" != true ]]; then
  echo "Product PR changed to an invalid state during validation" >&2
  exit 1
fi

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git fetch origin \
  "+refs/heads/main:refs/remotes/origin/main" \
  "+refs/heads/$PRODUCT_BRANCH:refs/remotes/origin/$PRODUCT_BRANCH"
git checkout -B oncotracer-v2-release-integration origin/main
STARTING_MAIN="$(git rev-parse HEAD)"
test "$STARTING_MAIN" = "$(branch_sha main)"
test "$(git rev-parse "origin/$PRODUCT_BRANCH")" = "$CANDIDATE_SHA"
if [[ "$PR_MERGED" == true ]]; then
  git merge-base --is-ancestor "$CANDIDATE_SHA" HEAD
  INTEGRATION_MESSAGE='Finalize OncoTracer v2.0.0 release gate on main'
else
  git merge --no-ff --no-commit "origin/$PRODUCT_BRANCH"
  INTEGRATION_MESSAGE='Release OncoTracer v2.0.0 candidate to main'
fi

python3 - <<'PY'
from pathlib import Path

release = Path('.github/workflows/release-v2.yml')
text = release.read_text(encoding='utf-8')
replacements = {
    'Resolve all successful push gates for the exact current main SHA':
        'Resolve all successful exact-main gates for the current main SHA',
    '/actions/runs?branch=main&event=push&status=completed&per_page=100':
        '/actions/runs?branch=main&status=completed&per_page=100',
    '.event == "push" and':
        '(.event == "push" or .event == "workflow_dispatch") and',
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'unexpected release gate occurrence for {old!r}: {count}')
    text = text.replace(old, new)
release.write_text(text, encoding='utf-8')

docs = Path('docs/parity_release.md')
text = docs.read_text(encoding='utf-8')
old = ('The release workflow verifies that Native v2 CI and both named parity workflows '
       'succeeded as push runs for the same exact current `main` SHA.')
new = ('The release workflow verifies that Native v2 CI and both named parity workflows '
       'succeeded for the same exact current `main` SHA, either as normal push gates or '
       'as explicitly dispatched exact-main gates from the trusted release controller.')
if text.count(old) != 1:
    raise SystemExit('unexpected parity release documentation contract')
docs.write_text(text.replace(old, new), encoding='utf-8')
PY

rm -f \
  .github/workflows/scheduled-v2-release-validation.yml \
  .github/v2-release-controller.sh
test ! -e .github/workflows/bootstrap-native-v2.yml
git add -A
git diff --cached --check
git status --short
git commit -m "$INTEGRATION_MESSAGE"
FINAL_MAIN_SHA="$(git rev-parse HEAD)"
git merge-base --is-ancestor "$CANDIDATE_SHA" "$FINAL_MAIN_SHA"
git push origin HEAD:main
test "$(branch_sha main)" = "$FINAL_MAIN_SHA"
comment_validation "Integrated validated candidate \`$CANDIDATE_SHA\` into clean release commit \`$FINAL_MAIN_SHA\`; temporary orchestration was removed before main validation."

dispatch_gate_set main "$FINAL_MAIN_SHA" 'exact-main'
comment_validation "<!-- v2-main-validation-complete:$FINAL_MAIN_SHA -->
All three exact-main gates passed for \`$FINAL_MAIN_SHA\`."

release_start="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gh workflow run release-v2.yml --ref main --repo "$GITHUB_REPOSITORY"
RELEASE_RUN_ID="$(resolve_run release-v2.yml main "$FINAL_MAIN_SHA" "$release_start")"
comment_validation "Dispatched stable release workflow: https://github.com/$GITHUB_REPOSITORY/actions/runs/$RELEASE_RUN_ID"
gh run watch "$RELEASE_RUN_ID" --exit-status --repo "$GITHUB_REPOSITORY"

test "$(branch_sha main)" = "$FINAL_MAIN_SHA"
gh release view v2.0.0 --repo "$GITHUB_REPOSITORY" \
  --json tagName,isDraft,isPrerelease,targetCommitish,assets \
  > "$RUNNER_TEMP/v2-release.json"
jq -e --arg sha "$FINAL_MAIN_SHA" '
  .tagName == "v2.0.0" and .isDraft == false and
  .isPrerelease == false and .targetCommitish == $sha and
  ([.assets[].name] | sort) ==
    (["SHA256SUMS","oncotracer","oncotracer-v2.0.0-parity-audit.tar.gz","release-provenance.json"] | sort)
' "$RUNNER_TEMP/v2-release.json"
comment_validation "<!-- v2-release-complete:$FINAL_MAIN_SHA -->
Released stable OncoTracer \`v2.0.0\` from exact validated commit \`$FINAL_MAIN_SHA\`."
gh pr close "$VALIDATION_PR" --repo "$GITHUB_REPOSITORY" \
  --comment 'Validation-only orchestration completed; this PR is not merged.' || true
gh issue close 29 --repo "$GITHUB_REPOSITORY" \
  --comment 'Audited v2 repair and release orchestration completed.' || true
