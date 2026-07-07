来源 (Source)
------------
E:\WindowsWorkspace\omnicompany\docs\.git

这是一个嵌套在外层仓 docs/ 目录下的独立 git 仓库(内层仓)。外层仓根为
E:\WindowsWorkspace\omnicompany,docs/ 下的所有真实文件(ARCHITECTURE.md、
plans/、projects/、standards/、tech_debt/ 等)都是作为外层仓的普通 blob 被
正常跟踪的 —— 外层索引里没有 gitlink(160000)条目,也没有 .gitmodules,证明
这个内层仓与外层仓的版本历史完全无关、未被有意用作子模块。

内层仓侦察结论
--------------
- 内层仓只有 2 个提交(分支 master,无 remote):
    6d86f38 feat: auto-generate staged-preview.html from verified config
    fe89c62 feat: auto-generate staged-preview.html from verified config
- 两次提交唯一涉及的文件是 staged-preview.html(某个发布/预览工具的自动生成
  残留产物),该文件在当前工作树中已不存在。
- 内层仓的 status 显示 docs/ 下几乎所有真实内容(ARCH-CHANGES.jsonl、
  ARCHITECTURE.md、PROGRESS.md、plans/、projects/、standards/、tech_debt/
  等)相对内层仓而言全部是 untracked —— 也就是说内层仓从未真正记录过 docs/
  的实际内容,只留下了两条与当前内容无关的 staged-preview.html 生成记录。
- 结论:没有发现"只存在于内层仓、外层仓没有"的独有历史或有价值内容。
  内层仓是发布工具跑起来时误留下的残留物,安全清除。

移出时间
--------
2026-07-04

为什么移出
----------
用户裁决清除:嵌套的 docs/.git 会污染 docs 根目录下任何项目的 git 历史
查询——在 docs/ 或其子目录下运行 `git log` / `git rev-parse --show-toplevel`
等命令时,可能被这个内层仓截胡,导致查到的是内层仓的(几乎空的、无关的)
历史,而不是外层 omnicompany 仓的真实提交历史。属于历史遗留的工具残留,
无保留价值。

本次操作
--------
- 未在外层仓做任何 git commit / add / rm 操作(严格只做文件系统层面的移动)。
- 仅将 docs/.git 整个目录改名移动(mv,非 delete)到本目录下的 git/ 子目录,
  完整保留可逆性;如需恢复,把本目录下的 git/ 移回
  E:\WindowsWorkspace\omnicompany\docs\.git 即可还原原状。
- 检查过 docs/ 下没有内层仓专属的 .gitignore / .gitattributes 文件(未发现,
  故无需一并归档)。

归档位置
--------
E:\WindowsWorkspace\omnicompany\_archive\docs-nested-git-20260704\git\
(即原 docs\.git 目录的完整内容,原样搬移)
