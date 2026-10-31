#!/usr/bin/env bash
# 给 main 配分支保护：必须走 PR、必须 CI 绿、禁止强推。
#
# 用法：
#   GH_TOKEN=ghp_xxx ./tools/ci/setup_branch_protection.sh
#   GH_TOKEN=ghp_xxx ./tools/ci/setup_branch_protection.sh --check   # 只看现状
#
# GH_TOKEN 需要 repo 权限（classic）或 Repository administration: write（fine-grained）。
# 没有 token 就在网页配，等价路径见文件末尾。
#
# **为什么不用「需要 1 个 approval」**：个人仓库里 PR 都是自己开的，
# 自己不能批准自己的 PR，设成 1 会让所有 PR 永久卡住。
# 真正起作用的门禁是「必须走 PR」+「check 必须绿」这两条。
set -euo pipefail

REPO="${REPO:-MultiLi/HealthDash}"
BRANCH="${BRANCH:-main}"
API="https://api.github.com/repos/${REPO}/branches/${BRANCH}/protection"
CHECK_NAME="${CHECK_NAME:-check}"

if [ -z "${GH_TOKEN:-}" ]; then
  echo "缺 GH_TOKEN。生成方式：GitHub → Settings → Developer settings → Personal access tokens"
  echo "（勾 repo 权限即可）。或者照着文件末尾的网页路径手点。"
  exit 2
fi

auth=(-H "Authorization: Bearer ${GH_TOKEN}" -H "Accept: application/vnd.github+json"
      -H "X-GitHub-Api-Version: 2022-11-28")

show() {
  echo "── 当前 ${REPO}:${BRANCH} 的保护状态 ──"
  if ! curl -sS -w '\n%{http_code}' "${auth[@]}" "$API" -o /tmp/_bp.json | tail -1 | grep -q '^200$'; then
    echo "尚未配置保护（404），或 token 权限不足。"
    return 0
  fi
  python3 - <<'PY'
import json
d = json.load(open("/tmp/_bp.json"))
rc = d.get("required_status_checks") or {}
rev = d.get("required_pull_request_reviews")
print("  必须走 PR          :", "是" if rev is not None else "否")
print("  必须 review 通过   :", (f"是（{rev.get('required_approving_review_count')} 人）" if rev else "否"))
print("  必须状态检查通过   :", "、".join(rc.get("contexts") or []) or "无")
print("  分支必须最新       :", "是" if rc.get("strict") else "否")
print("  管理员也受限       :", "是" if d.get("enforce_admins", {}).get("enabled") else "否")
print("  允许强推           :", "是" if d.get("allow_force_pushes", {}).get("enabled") else "否")
print("  允许删除分支       :", "是" if d.get("allow_deletions", {}).get("enabled") else "否")
PY
}

if [ "${1:-}" = "--check" ]; then
  show
  exit 0
fi

echo "配置 ${REPO}:${BRANCH} …"
CODE=$(curl -sS -o /tmp/_bp_resp.json -w '%{http_code}' -X PUT "$API" \
  "${auth[@]}" \
  -d "$(cat <<JSON
{
  "required_status_checks": { "strict": true, "contexts": ["${CHECK_NAME}"] },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
)")

if [ "$CODE" != "200" ]; then
  echo "失败（HTTP ${CODE}）："
  cat /tmp/_bp_resp.json
  echo ""
  echo "常见原因：① token 没有 administration 权限；② 该分支还没有任何 PR/CI 运行记录，"
  echo "导致 status check 名 '${CHECK_NAME}' 不存在——先把 ci.yml 推上去跑绿一次再来配。"
  exit 1
fi

echo "已配置。"
show
echo ""
echo "回滚：curl -X DELETE ${API} -H 'Authorization: Bearer $GH_TOKEN'"
echo ""
cat <<'EOF'
── 网页等价路径（不想给 token 时手点）────────────────────────────
  1. https://github.com/MultiLi/HealthDash/settings/branches
  2. Add branch protection rule
  3. Branch name pattern: main
  4. 勾选 Require a pull request before merging
     └ 不要勾 Require approvals（个人仓库会把自己卡死）
  5. 勾选 Require status checks to pass before merging
     └ 搜索并选中 `check`
     └ 勾上 Require branches to be up to date before merging
  6. 勾选 Do not allow bypassing the above settings（否则管理员可绕过）
  7. 确保 Allow force pushes / Allow deletions 都不勾
  8. Create
────────────────────────────────────────────────────────────────
EOF
