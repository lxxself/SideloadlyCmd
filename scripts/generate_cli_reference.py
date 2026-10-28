#!/usr/bin/env python3
"""Generate public CLI help from the parser without executing commands."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'src'))
from sideloadlycmd.cli import build_parser


def render():
    parser=build_parser()
    parsers=next(action.choices for action in parser._actions if isinstance(action,argparse._SubParsersAction))
    header='# CLI 命令参考\n\n本文件由 `python scripts/generate_cli_reference.py` 从实际 parser 生成。不要手改参数段。流程见 [usage.md](usage.md)。\n\n'
    header+='## 退出码\n\n- 0：本条命令完成；登记成功不代表实际续期已验收。\n- 3：部分完成，例如设备复核或临时任务清理尚未确认。\n- 2：参数错误。\n- 10：原生预检或状态文件问题；11–13：桌面后端或自动化检查问题。\n- 20：IPA 无效。\n- 30：签名或安装报告失败。\n- 31：超时，不能据此断言设备未改变。\n- 32：需要用户介入或操作取消。\n- 33：身份、绑定或结果无法确认。\n\n'
    header+='原生命令：sideload（一键签名安装）、teams（团队列表）、native-doctor、prepare、install-prepared、verify-prepared、background-run、background-keychain、refresh-register、refresh-status、refresh-native、refresh-scheduler。\n\n桌面兼容：doctor、status、install、verify，依赖 Sideloadly GUI。inspect 是只读 IPA 检查。headless-doctor、headless-plan、实验 login 不是正常账号管理流程。\n\n'
    parts=[header,'## 全局入口\n\n```text\n'+parser.format_help()+'```\n']
    for name,command in parsers.items():
        parts.append('\n## '+name+'\n\n```text\n'+command.format_help()+'```\n')
    return ''.join(parts)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    target=ROOT/'docs/cli-reference.md'
    result=render()
    if args.check:
        if not target.exists() or target.read_text()!=result:
            print('CLI reference is stale; run python scripts/generate_cli_reference.py',file=sys.stderr)
            return 1
    else: target.write_text(result)
    return 0


if __name__=='__main__': raise SystemExit(main())
