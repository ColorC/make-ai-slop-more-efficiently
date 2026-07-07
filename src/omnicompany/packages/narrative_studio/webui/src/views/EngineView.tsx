// 落地结构演算引擎:带内部 tab 的壳,把路线节点图 / 数值状态 / 演练三个子视图聚到一处。
// 说明交互结构如何算出可玩流;当前路线/数值/结局未认可故多为空。
import React, { useState } from "react";
import { RouteGraphView } from "./RouteGraphView";
import { VariablesView } from "./VariablesView";
import { PlaythroughView } from "./PlaythroughView";

type Tab = "route" | "variables" | "playthrough";

const TABS: { id: Tab; label: string }[] = [
  { id: "route", label: "路线节点图" },
  { id: "variables", label: "数值 / 状态" },
  { id: "playthrough", label: "演练" },
];

export function EngineView({ onChanged }: { onChanged?: () => void }) {
  const [tab, setTab] = useState<Tab>("route");

  return (
    <div>
      <h2 className="view-h">落地结构演算引擎</h2>
      <p className="view-sub">交互结构如何算出可玩流 —— 路线决定走法、数值决定状态、演练把两者跑成一条具体轨迹。当前路线 / 数值 / 结局未认可，故多为空。</p>

      <div className="row" style={{ marginBottom: 12 }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? "primary" : ""}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "route" && <RouteGraphView onChanged={onChanged} />}
      {tab === "variables" && <VariablesView onChanged={onChanged} />}
      {tab === "playthrough" && <PlaythroughView onChanged={onChanged} />}
    </div>
  );
}
