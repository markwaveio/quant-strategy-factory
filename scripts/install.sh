#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

UPDATE_MODE=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --update|-u) UPDATE_MODE=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help)
      echo "用法: bash scripts/install.sh [--update] [--check]"
      echo "  默认      创建 .venv,安装包,安装 agent skills,运行自检"
      echo "  --update  覆盖更新已安装的 QSF skills"
      echo "  --check   不安装,只检查当前环境和运行测试"
      exit 0 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

ok(){ printf "  OK  %s\n" "$1"; }
warn(){ printf "  WARN  %s\n" "$1"; }
fail(){ printf "  FAIL  %s\n" "$1"; exit 1; }

expand_path(){
  case "$1" in
    "~") printf "%s\n" "$HOME" ;;
    "~/"*) printf "%s/%s\n" "$HOME" "${1#~/}" ;;
    *) printf "%s\n" "$1" ;;
  esac
}

detect_skills_dir(){
  if [ "${SKILLS_DIR:-auto}" != "auto" ] && [ -n "${SKILLS_DIR:-}" ]; then
    expand_path "$SKILLS_DIR"
    return
  fi
  if [ -d "$HOME/.claude" ]; then printf "%s\n" "$HOME/.claude/skills"; return; fi
  if [ -d "$HOME/.codex" ]; then printf "%s\n" "$HOME/.codex/skills"; return; fi
  if [ -d "$HOME/.openclaw" ]; then printf "%s\n" "$HOME/.openclaw/skills"; return; fi
  printf "%s\n" "$HOME/.codex/skills"
}

if [ -f "$ROOT/config/qsf.config" ]; then
  set -a
  . "$ROOT/config/qsf.config"
  set +a
elif [ -f "$ROOT/config/qsf.config.example" ]; then
  warn "未找到 config/qsf.config,使用模板默认值。需要自定义时先复制模板。"
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_OPEND="${INSTALL_OPEND:-true}"
RUN_TESTS="${RUN_TESTS:-true}"
RUN_SMOKE="${RUN_SMOKE:-true}"
SMOKE_CONFIG="${SMOKE_CONFIG:-configs/default.yaml}"
DEFAULT_CONFIG="${DEFAULT_CONFIG:-configs/opend-hk-daily-research.yaml}"
WORKSPACE_DIR="$(expand_path "${WORKSPACE_DIR:-~/quant-strategy-factory-workspace}")"
SKILLS_DIR="$(detect_skills_dir)"

echo "QSF 学员部署"
echo "ROOT=$ROOT"
echo "SKILLS_DIR=$SKILLS_DIR"
echo "WORKSPACE_DIR=$WORKSPACE_DIR"
echo "INSTALL_OPEND=$INSTALL_OPEND"
echo "SMOKE_CONFIG=$SMOKE_CONFIG"
echo "DEFAULT_CONFIG=$DEFAULT_CONFIG"

command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "找不到 Python:$PYTHON_BIN"
ok "$($PYTHON_BIN --version 2>&1)"

if [ "$CHECK_ONLY" -eq 0 ]; then
  if [ ! -d "$ROOT/.venv" ]; then
    "$PYTHON_BIN" -m venv "$ROOT/.venv"
    ok "已创建 .venv"
  else
    ok ".venv 已存在"
  fi

  if [ "$INSTALL_OPEND" = "true" ]; then
    "$ROOT/.venv/bin/pip" install -e ".[dev,opend]"
  else
    "$ROOT/.venv/bin/pip" install -e ".[dev]"
  fi
  ok "Python 包已安装"

  mkdir -p "$SKILLS_DIR" "$WORKSPACE_DIR"/{reports,data}
  for src in "$ROOT/skills/qsf-student-installer" "$ROOT/.claude/skills/"*; do
    [ -d "$src" ] || continue
    name="$(basename "$src")"
    dst="$SKILLS_DIR/$name"
    if [ -d "$dst" ]; then
      if [ "$UPDATE_MODE" -eq 1 ]; then
        rm -rf "$dst"
        cp -R "$src" "$dst"
        ok "skill 已更新:$name"
      else
        warn "skill 已存在,跳过:$name"
      fi
    else
      cp -R "$src" "$dst"
      ok "skill 已安装:$name"
    fi
  done
fi

export PYTHONPATH="$ROOT/src"

if [ "$RUN_TESTS" = "true" ]; then
  "$ROOT/.venv/bin/ruff" check src tests
  "$ROOT/.venv/bin/pytest"
  ok "测试通过"
fi

if [ "$RUN_SMOKE" = "true" ]; then
  "$ROOT/.venv/bin/python" -m qsf.cli run \
    --config "$SMOKE_CONFIG" \
    --report "$WORKSPACE_DIR/reports/decision-smoke.md" \
    --research-report "$WORKSPACE_DIR/reports/research-smoke.md" \
    --strategies "$WORKSPACE_DIR/reports/strategies-smoke.json" >/tmp/qsf-install-smoke.log
  ok "离线 smoke run 通过:$WORKSPACE_DIR/reports"
fi

echo "默认真实数据配置:$DEFAULT_CONFIG"
echo "完成。重启或刷新你的 AI 助手后,可说:使用 qsf-student-installer 帮我跑一轮量化策略工厂。"
