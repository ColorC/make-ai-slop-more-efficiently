# 输入同条件复核

`manual-baseline-card.json` 的人工路径读取了 Gate 2 路线中的目标名称、动作类型、源像素框和证据 step。`hypothesis-run-01` 与 `hypothesis-run-02` 只在 goal 中收到六个目标名称，未收到既有源像素框与已验证结果。

因此前两次运行可用于定位 Experimenter、validator 和坐标理解失败，不能作为第 7.2 节的同条件严格占优证明。下一次配对必须通过显式 `prior_verified_targets` 合同把人工路径已经使用的既有事实提供给 B 路径；这些事实仍只用于内部候选生成，输出继续接受原图可见性复核和独立评分。