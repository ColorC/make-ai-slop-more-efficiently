//! progress-service — OmniCompany 的进度唯一真源 Rust 服务（统一数据模型 + 统一身份）。
//!
//! 模型层级：cluster(域) → goal(主线/北极星 | 支线) → task(计划) → task(子计划) + progress(进度历史)。
//! 统一身份：每条 task 带 external_refs（meego:<id> / multica:<id> / local:<id>），同一张单子只一条 task；
//!   meego 与 multica 指向同一 bug（按 BUG-<id> 关键字）时合并成一条，绝不双份。
//! 渠道：channel=local/meego/multica，标明接单/反馈走哪条。
//! 持久化：JSON 文件（现有 whatnow.json 文件名暂保留兼容），后面再迁 SQLite。
//! 消费方：dashboard(8210) 任务窗口 / overlay-shell 面板通过 HTTP(:8230) 读。

use axum::{
    extract::{Query, State},
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use tower_http::cors::{Any, CorsLayer};

const PORT: u16 = 8230;

fn now_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

// ── 数据模型 ────────────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone, Default)]
struct Cluster {
    id: String,
    title: String,
    #[serde(default)]
    note: String,
    #[serde(default)]
    ord: i32,
}

#[derive(Serialize, Deserialize, Clone, Default)]
struct Goal {
    id: String,
    #[serde(default)]
    cluster_id: String,
    title: String,
    /// 北极星 / 北极星(更远) / 里程碑
    #[serde(default)]
    kind: String,
    /// main(主线) | side(支线)
    #[serde(default = "default_main")]
    line: String,
    #[serde(default)]
    status: String,
    /// 长期目标（通关条件）
    #[serde(default)]
    objective: String,
    #[serde(default)]
    detail: String,
    /// wishlist / derived(主题归纳新开支线) / manual
    #[serde(default)]
    source: String,
    /// 对应的计划目录（docs/plans 下相对路径）——宪章型任务线挂上后前端可「复制路径」。
    #[serde(default)]
    plan_id: String,
    #[serde(default)]
    ord: i32,
}
fn default_main() -> String { "main".into() }

#[derive(Serialize, Deserialize, Clone, Default)]
struct Task {
    id: String,
    #[serde(default)]
    goal_id: Option<String>,
    #[serde(default)]
    parent_task_id: Option<String>,
    title: String,
    #[serde(default)]
    status: String,
    /// 0-100
    #[serde(default)]
    completion: i32,
    #[serde(default = "default_main")]
    line: String,
    /// local | meego | multica
    #[serde(default = "default_local")]
    channel: String,
    /// 统一身份：meego:<id> / multica:<id> / local:<id>，可多条（同一单子两个系统）
    #[serde(default)]
    external_refs: Vec<String>,
    #[serde(default)]
    assignee: Option<String>,
    #[serde(default)]
    due_date: Option<String>,
    /// 关联的 omnicompany plan id（R2/R3 用）
    #[serde(default)]
    plan_id: Option<String>,
    #[serde(default)]
    latest_progress: Option<String>,
    #[serde(default)]
    archived: bool,
    #[serde(default)]
    created_at: i64,
    #[serde(default)]
    updated_at: i64,
    // ── 执行子任务字段(TASK-SSOT-UNIFICATION 2026-07-05)：计划拆出的执行工单挂
    //    parent_task_id 成为计划 task 的子 task，这些字段只有执行子任务用。
    //    全部可缺省 + 空值不序列化：存量 671 条老数据零迁移、whatnow.json 不膨胀。
    #[serde(default, skip_serializing_if = "String::is_empty")]
    description: String,
    /// 自包含执行细节（BMAD story 理念）
    #[serde(default, skip_serializing_if = "String::is_empty")]
    details: String,
    /// 怎么验证它做完了
    #[serde(default, skip_serializing_if = "String::is_empty")]
    test_strategy: String,
    /// 前置执行子任务的 id（同计划内的局部序号）
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    dependencies: Vec<String>,
    /// high | medium | low
    #[serde(default, skip_serializing_if = "String::is_empty")]
    priority: String,
    /// 这件事会碰哪些文件/目录（判并行用）
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    file_scope: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    expected_outputs: Vec<String>,
    /// 边做边记的进度条目 [{ts, text}, ...]
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    notes: Vec<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    complexity: Option<i64>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    parallel: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    workload: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    difficulty: Option<i64>,
    /// 拆分器给出的拆分理由
    #[serde(default, skip_serializing_if = "String::is_empty")]
    reasoning: String,
}
fn default_local() -> String { "local".into() }

/// 状态是否"已完成/已结单"（去重/归档/收件箱过滤用；兼容中英文渠道状态）。
fn is_done_status(s: &str) -> bool {
    let t = s.trim().to_lowercase();
    const DONE: &[&str] = &["done", "closed", "cancelled", "canceled", "completed", "resolved", "merged",
        "已完成", "已关闭", "已取消", "已解决", "完成", "关闭", "取消", "解决", "下线", "已上线", "已发布"];
    DONE.iter().any(|d| t == *d || t.contains(d))
}

/// bug 分级（收件箱紧急度排序用）：必现/P0/high=3，偶现/medium=2，其它=1。
fn bug_grade(title: &str, status: &str) -> i32 {
    let t = format!("{} {}", title, status).to_lowercase();
    if t.contains("必现") || t.contains("p0") || t.contains("blocker") || t.contains("urgent") || t.contains("critical") || t.contains("high") { 3 }
    else if t.contains("偶现") || t.contains("medium") || t.contains("p1") { 2 }
    else { 1 }
}

#[derive(Serialize, Deserialize, Clone, Default)]
struct Progress {
    #[serde(default)]
    id: i64,
    /// goal | task
    subject_kind: String,
    subject_id: String,
    #[serde(default)]
    ts: i64,
    text: String,
    #[serde(default)]
    source: String,
}

/// 旧"当前专注"条目，仅保留用于把老数据一次性迁移到 pins。
#[derive(Serialize, Deserialize, Clone, Default)]
struct FocusItem {
    task_id: String,
    #[serde(default)]
    note: String,
    #[serde(default)]
    set_at: i64,
}

/// 置顶条目——取代旧的"当前专注"。任务线(goal)与具体任务(task)都能置顶。
#[derive(Serialize, Deserialize, Clone, Default)]
struct Pin {
    /// "goal" | "task"
    #[serde(default = "default_task_kind")]
    subject_kind: String,
    subject_id: String,
    #[serde(default)]
    note: String,
    #[serde(default)]
    set_at: i64,
}
fn default_task_kind() -> String { "task".into() }

#[derive(Serialize, Deserialize, Clone, Default)]
struct Store {
    #[serde(default)]
    clusters: Vec<Cluster>,
    #[serde(default)]
    goals: Vec<Goal>,
    #[serde(default)]
    tasks: Vec<Task>,
    #[serde(default)]
    progress: Vec<Progress>,
    /// 旧字段(只 task)，仅用于一次性迁移到 pins；迁移后清空、不再写盘。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    focus: Vec<FocusItem>,
    /// 置顶：任务线(goal) / 具体任务(task) 都可，最新置顶在前。取代旧"当前专注"。
    #[serde(default)]
    pins: Vec<Pin>,
    #[serde(default)]
    seq: i64,
}

struct App {
    store: Mutex<Store>,
    path: std::path::PathBuf,
}
type Db = Arc<App>;

impl App {
    fn load(path: std::path::PathBuf) -> Self {
        let mut store = std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str::<Store>(&s).ok())
            .unwrap_or_default();
        // 一次性迁移：旧"当前专注"(focus, 只 task) → 通用置顶(pins)。
        let migrated = !store.focus.is_empty() && store.pins.is_empty();
        if migrated {
            store.pins = store.focus.iter().map(|f| Pin {
                subject_kind: "task".into(),
                subject_id: f.task_id.clone(),
                note: f.note.clone(),
                set_at: if f.set_at > 0 { f.set_at } else { now_ms() },
            }).collect();
        }
        store.focus.clear(); // 旧字段不再使用
        let app = App { store: Mutex::new(store), path };
        if migrated {
            let snap = app.store.lock().unwrap().clone();
            app.save(&snap); // 落盘迁移结果，清掉旧 focus
        }
        app
    }
    fn save(&self, s: &Store) {
        if let Ok(txt) = serde_json::to_string_pretty(s) {
            let tmp = self.path.with_extension("json.tmp");
            if std::fs::write(&tmp, &txt).is_ok() {
                let _ = std::fs::rename(&tmp, &self.path);
            }
        }
    }
}

// ── 工具：BUG-<id> 关键字提取（统一身份去重的核心） ───────────────────────────

/// 从标题里抽出 BUG-数字 关键字（meego 与 multica 指同一 bug 时据此合并）。
fn bug_key(title: &str) -> Option<String> {
    let up = title.to_uppercase();
    let idx = up.find("BUG-")?;
    let rest = &up[idx + 4..];
    let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
    if digits.len() >= 6 { Some(format!("BUG-{}", digits)) } else { None }
}

// ── upsert：按 external_ref 或 bug_key 合并，杜绝双份 ─────────────────────────

struct IncomingTask {
    title: String,
    status: String,
    channel: String,
    external_ref: String,
    assignee: Option<String>,
    due_date: Option<String>,
    completion: i32,
}

fn upsert_external(s: &mut Store, inc: IncomingTask) -> String {
    let key = bug_key(&inc.title);
    // 1) 已有同 external_ref → 更新
    if let Some(t) = s.tasks.iter_mut().find(|t| t.external_refs.iter().any(|r| r == &inc.external_ref)) {
        t.title = inc.title;
        t.status = inc.status;
        if inc.assignee.is_some() { t.assignee = inc.assignee; }
        if inc.due_date.is_some() { t.due_date = inc.due_date; }
        t.updated_at = now_ms();
        return t.id.clone();
    }
    // 2) 同 bug_key 的另一渠道单子 → 合并（统一身份：一张单子两个外部 ref）
    if let Some(k) = key.clone() {
        if let Some(t) = s.tasks.iter_mut().find(|t| {
            t.external_refs.iter().any(|r| r != &inc.external_ref)
                && bug_key(&t.title).as_deref() == Some(k.as_str())
        }) {
            if !t.external_refs.contains(&inc.external_ref) {
                t.external_refs.push(inc.external_ref.clone());
            }
            // 渠道标成多源
            if t.channel != inc.channel && t.channel != "multi" {
                t.channel = "multi".into();
            }
            if inc.assignee.is_some() { t.assignee = inc.assignee; }
            if inc.due_date.is_some() { t.due_date = inc.due_date; }
            t.updated_at = now_ms();
            return t.id.clone();
        }
    }
    // 3) 新建
    s.seq += 1;
    let id = format!("t{}", s.seq);
    let t = Task {
        id: id.clone(),
        title: inc.title,
        status: inc.status,
        completion: inc.completion,
        channel: inc.channel,
        external_refs: vec![inc.external_ref],
        assignee: inc.assignee,
        due_date: inc.due_date,
        created_at: now_ms(),
        updated_at: now_ms(),
        line: "side".into(),
        ..Default::default()
    };
    s.tasks.push(t);
    id
}

// ── 处理器 ───────────────────────────────────────────────────────────────────

async fn health() -> &'static str { "ok" }

/// 完整看板树：cluster → goal → task(主) → 子 task + 每条的进度历史 + pins。
/// `?archived=1` 时连归档任务一起返回（每条带 archived 标记）；置顶任务即便归档也始终返回。
async fn board(State(db): State<Db>, Query(q): Query<HashMap<String, String>>) -> Json<Value> {
    let s = db.store.lock().unwrap();
    let include_archived = matches!(q.get("archived").map(|v| v.as_str()), Some("1") | Some("true") | Some("yes"));
    let pinned_tasks: std::collections::HashSet<&str> = s.pins.iter()
        .filter(|p| p.subject_kind == "task").map(|p| p.subject_id.as_str()).collect();
    let prog_of = |kind: &str, id: &str| -> Vec<Value> {
        let mut v: Vec<&Progress> = s.progress.iter().filter(|p| p.subject_kind == kind && p.subject_id == id).collect();
        v.sort_by(|a, b| b.ts.cmp(&a.ts));
        v.into_iter().map(|p| json!({"ts":p.ts,"text":p.text,"source":p.source})).collect()
    };
    let task_json = |t: &Task| -> Value {
        let subs: Vec<Value> = s.tasks.iter().filter(|c| c.parent_task_id.as_deref() == Some(t.id.as_str()) && !c.archived)
            .map(|c| json!({
                "id":c.id,"title":c.title,"status":c.status,"completion":c.completion,
                "channel":c.channel,"external_refs":c.external_refs,"assignee":c.assignee,
                "due_date":c.due_date,"plan_id":c.plan_id,"latest_progress":c.latest_progress,
                "progress": prog_of("task",&c.id),
            })).collect();
        json!({
            "id":t.id,"title":t.title,"status":t.status,"completion":t.completion,"line":t.line,
            "channel":t.channel,"external_refs":t.external_refs,"assignee":t.assignee,
            "due_date":t.due_date,"plan_id":t.plan_id,"latest_progress":t.latest_progress,
            "updated_at":t.updated_at,"archived":t.archived,
            "subtasks": subs,
            "progress": prog_of("task",&t.id),
        })
    };
    let goal_json = |g: &Goal| -> Value {
        // 显示规则：未归档 + 置顶的(即便已归档) + (勾选显示归档时)全部归档的。
        let mut tasks: Vec<&Task> = s.tasks.iter()
            .filter(|t| t.goal_id.as_deref() == Some(g.id.as_str()) && t.parent_task_id.is_none()
                && (!t.archived || include_archived || pinned_tasks.contains(t.id.as_str())))
            .collect();
        tasks.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
        // 这条任务线总共归档了多少条（即便此次没返回，前端也能提示"N 条已归档"）。
        let archived_count = s.tasks.iter()
            .filter(|t| t.goal_id.as_deref() == Some(g.id.as_str()) && t.parent_task_id.is_none() && t.archived)
            .count();
        json!({
            "id":g.id,"title":g.title,"kind":g.kind,"line":g.line,"status":g.status,
            "objective":g.objective,"detail":g.detail,"source":g.source,"cluster_id":g.cluster_id,
            "plan_id":g.plan_id,
            "archived_count": archived_count,
            "tasks": tasks.iter().map(|t| task_json(t)).collect::<Vec<_>>(),
            "progress": prog_of("goal",&g.id),
        })
    };
    let mut clusters: Vec<&Cluster> = s.clusters.iter().collect();
    clusters.sort_by_key(|c| c.ord);
    let clusters_json: Vec<Value> = clusters.iter().map(|c| {
        let mut goals: Vec<&Goal> = s.goals.iter().filter(|g| g.cluster_id == c.id).collect();
        goals.sort_by_key(|g| (g.line != "main", g.ord));
        json!({"id":c.id,"title":c.title,"note":c.note,
            "goals": goals.iter().map(|g| goal_json(g)).collect::<Vec<_>>()})
    }).collect();
    // 无 cluster 的支线目标单列
    let orphan_goals: Vec<Value> = s.goals.iter()
        .filter(|g| g.cluster_id.is_empty() || !s.clusters.iter().any(|c| c.id == g.cluster_id))
        .map(|g| goal_json(g)).collect();
    // 外部收件箱（无归属、未归档、未完成的单子）：按紧急度排序（排期 + bug 分级），干掉已完成的。
    let mut loose_refs: Vec<&Task> = s.tasks.iter()
        .filter(|t| t.goal_id.is_none() && t.parent_task_id.is_none() && !t.archived && !is_done_status(&t.status))
        .collect();
    loose_refs.sort_by(|a, b| {
        let due_a = a.due_date.clone().unwrap_or_default();
        let due_b = b.due_date.clone().unwrap_or_default();
        // 有排期的优先（按 due 升序，越早越急），无排期的排后；同档按 bug 分级降序。
        let key = |due: &str, t: &Task| (due.is_empty(), due.to_string(), -bug_grade(&t.title, &t.status));
        key(&due_a, a).cmp(&key(&due_b, b))
    });
    let loose: Vec<Value> = loose_refs.iter().map(|t| task_json(t)).collect();
    // 置顶（任务线 goal / 具体任务 task）。解析出标题/进度等，让前端直接渲染成一行；
    // 解析不到(主体已删)标 missing=true，前端可显示"已失效，点取消置顶"。
    let pin_json = |p: &Pin| -> Value {
        if p.subject_kind == "goal" {
            if let Some(g) = s.goals.iter().find(|g| g.id == p.subject_id) {
                let gtasks: Vec<&Task> = s.tasks.iter()
                    .filter(|t| t.goal_id.as_deref() == Some(g.id.as_str()) && t.parent_task_id.is_none() && !t.archived)
                    .collect();
                let pct = if gtasks.is_empty() { 0 } else { gtasks.iter().map(|t| t.completion).sum::<i32>() / gtasks.len() as i32 };
                let done = gtasks.iter().filter(|t| is_done_status(&t.status)).count();
                return json!({"subject_kind":"goal","subject_id":g.id,"note":p.note,"set_at":p.set_at,
                    "title":g.title,"kind":g.kind,"line":g.line,"completion":pct,
                    "task_count":gtasks.len(),"done_count":done,"missing":false});
            }
        } else if let Some(t) = s.tasks.iter().find(|t| t.id == p.subject_id) {
            return json!({"subject_kind":"task","subject_id":t.id,"note":p.note,"set_at":p.set_at,
                "title":t.title,"line":t.line,"channel":t.channel,"completion":t.completion,
                "status":t.status,"plan_id":t.plan_id,"external_refs":t.external_refs,
                "latest_progress":t.latest_progress,"archived":t.archived,"missing":false});
        }
        json!({"subject_kind":p.subject_kind,"subject_id":p.subject_id,"note":p.note,"set_at":p.set_at,"title":Value::Null,"missing":true})
    };
    let pins: Vec<Value> = s.pins.iter().map(|p| pin_json(p)).collect();
    // 兼容旧消费方(老前端缓存)：focus = 仅 task 的置顶，沿用旧形态。
    let focus: Vec<Value> = s.pins.iter().filter(|p| p.subject_kind == "task").map(|p| {
        let t = s.tasks.iter().find(|t| t.id == p.subject_id);
        json!({"task_id":p.subject_id,"note":p.note,"set_at":p.set_at,
            "title": t.map(|t| t.title.clone()), "channel": t.map(|t| t.channel.clone())})
    }).collect();
    Json(json!({
        "clusters": clusters_json,
        "orphan_goals": orphan_goals,
        "loose_tasks": loose,
        "pins": pins,
        "focus": focus,
        "counts": {"clusters":s.clusters.len(),"goals":s.goals.len(),
            "tasks":s.tasks.iter().filter(|t| !t.archived).count(),
            "archived":s.tasks.iter().filter(|t| t.archived).count()},
        "updated_at": now_ms(),
    }))
}

/// 计划的执行子任务查询（TASK-SSOT-UNIFICATION）：lifecycle TaskStore 客户端的读路径。
/// `?plan_id=<docs/plans 相对路径>` → 该计划的 parent(计划级 task) + 全部执行子任务(含已归档)；
/// 不带 plan_id → 所有带 plan_id 的计划级 task 逐个分组返回（跨计划全量列取用）。
/// 返回的是完整 Task 序列化（含执行字段），不是 board 的轻量摘要。
async fn plan_tasks(State(db): State<Db>, Query(q): Query<HashMap<String, String>>) -> Json<Value> {
    let s = db.store.lock().unwrap();
    let want = q.get("plan_id").map(|x| x.as_str()).filter(|x| !x.is_empty());
    let parents: Vec<&Task> = s.tasks.iter()
        .filter(|t| t.parent_task_id.is_none())
        .filter(|t| t.plan_id.as_deref().map_or(false, |p| !p.is_empty()))
        .filter(|t| want.map_or(true, |w| t.plan_id.as_deref() == Some(w)))
        .collect();
    let plans: Vec<Value> = parents.iter().map(|p| {
        let kids: Vec<Value> = s.tasks.iter()
            .filter(|c| c.parent_task_id.as_deref() == Some(p.id.as_str()))
            .map(|c| serde_json::to_value(c).unwrap_or(Value::Null))
            .collect();
        json!({
            "plan_id": p.plan_id,
            "parent": serde_json::to_value(p).unwrap_or(Value::Null),
            "tasks": kids,
        })
    }).collect();
    Json(json!({"ok": true, "plans": plans}))
}

async fn upsert_cluster(State(db): State<Db>, Json(b): Json<Cluster>) -> Json<Value> {
    let mut s = db.store.lock().unwrap();
    if let Some(c) = s.clusters.iter_mut().find(|c| c.id == b.id) { *c = b; }
    else { s.clusters.push(b); }
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":true}))
}

async fn upsert_goal(State(db): State<Db>, Json(b): Json<Goal>) -> Json<Value> {
    let mut s = db.store.lock().unwrap();
    if let Some(g) = s.goals.iter_mut().find(|g| g.id == b.id) { *g = b; }
    else { s.goals.push(b); }
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":true}))
}

async fn upsert_task(State(db): State<Db>, Json(mut b): Json<Task>) -> Json<Value> {
    let mut s = db.store.lock().unwrap();
    if b.id.is_empty() { s.seq += 1; b.id = format!("t{}", s.seq); b.created_at = now_ms(); }
    b.updated_at = now_ms();
    if let Some(t) = s.tasks.iter_mut().find(|t| t.id == b.id) {
        b.created_at = t.created_at;
        *t = b.clone();
    } else { s.tasks.push(b.clone()); }
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":true,"id":b.id}))
}

async fn add_progress(State(db): State<Db>, Json(mut b): Json<Progress>) -> Json<Value> {
    let mut s = db.store.lock().unwrap();
    s.seq += 1; b.id = s.seq;
    if b.ts == 0 { b.ts = now_ms(); }
    // 同步成 task/goal 的最新进展
    let (k, id, text) = (b.subject_kind.clone(), b.subject_id.clone(), b.text.clone());
    s.progress.push(b);
    if k == "task" {
        if let Some(t) = s.tasks.iter_mut().find(|t| t.id == id) { t.latest_progress = Some(text); t.updated_at = now_ms(); }
    }
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":true}))
}

const PIN_LIMIT: usize = 30;
fn default_true() -> bool { true }

#[derive(Deserialize)]
struct PinReq {
    #[serde(default = "default_task_kind")]
    subject_kind: String,
    subject_id: String,
    #[serde(default)]
    note: String,
    /// true=置顶(默认) / false=取消置顶
    #[serde(default = "default_true")]
    pinned: bool,
}

async fn get_pins(State(db): State<Db>) -> Json<Value> {
    let s = db.store.lock().unwrap();
    Json(json!({"pins": s.pins}))
}

/// 置顶 / 取消置顶（任务线 goal 或具体任务 task）。pinned=false 即取消；同主体去重、最新在前。
async fn set_pin(State(db): State<Db>, Json(b): Json<PinReq>) -> Json<Value> {
    if b.subject_id.is_empty() { return Json(json!({"ok":false,"error":"missing subject_id"})); }
    let kind = if b.subject_kind == "goal" { "goal" } else { "task" };
    let mut s = db.store.lock().unwrap();
    s.pins.retain(|p| !(p.subject_id == b.subject_id && p.subject_kind == kind));
    let pinned = b.pinned;
    if pinned {
        s.pins.insert(0, Pin { subject_kind: kind.into(), subject_id: b.subject_id.clone(), note: b.note.clone(), set_at: now_ms() });
        s.pins.truncate(PIN_LIMIT);
    }
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":true,"pinned":pinned}))
}

// ── 兼容旧接口：/api/focus 等价于 pin 一个 task（老消费方/脚本不至于断） ──────────
async fn get_focus(State(db): State<Db>) -> Json<Value> {
    let s = db.store.lock().unwrap();
    let focus: Vec<Value> = s.pins.iter().filter(|p| p.subject_kind == "task")
        .map(|p| json!({"task_id":p.subject_id,"note":p.note,"set_at":p.set_at})).collect();
    Json(json!({"focus": focus}))
}
async fn set_focus(State(db): State<Db>, Json(b): Json<FocusItem>) -> Json<Value> {
    let mut s = db.store.lock().unwrap();
    s.pins.retain(|p| !(p.subject_id == b.task_id && p.subject_kind == "task"));
    s.pins.insert(0, Pin { subject_kind: "task".into(), subject_id: b.task_id, note: b.note, set_at: now_ms() });
    s.pins.truncate(PIN_LIMIT);
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":true}))
}

/// 整树 seed：clusters/goals/tasks/progress 批量灌入（R2/R3 的 Python feeder 用）。
async fn seed(State(db): State<Db>, Json(b): Json<Value>) -> Json<Value> {
    let mut s = db.store.lock().unwrap();
    if let Some(arr) = b.get("clusters").and_then(|v| v.as_array()) {
        for c in arr { if let Ok(c) = serde_json::from_value::<Cluster>(c.clone()) {
            if let Some(x) = s.clusters.iter_mut().find(|x| x.id == c.id) { *x = c; } else { s.clusters.push(c); }
        }}
    }
    if let Some(arr) = b.get("goals").and_then(|v| v.as_array()) {
        for g in arr { if let Ok(g) = serde_json::from_value::<Goal>(g.clone()) {
            if let Some(x) = s.goals.iter_mut().find(|x| x.id == g.id) { *x = g; } else { s.goals.push(g); }
        }}
    }
    let mut id_map: HashMap<String, String> = HashMap::new();
    if let Some(arr) = b.get("tasks").and_then(|v| v.as_array()) {
        for t in arr {
            if let Ok(mut t) = serde_json::from_value::<Task>(t.clone()) {
                let want = t.id.clone();
                if t.id.is_empty() { s.seq += 1; t.id = format!("t{}", s.seq); }
                if t.created_at == 0 { t.created_at = now_ms(); }
                t.updated_at = now_ms();
                if let Some(x) = s.tasks.iter_mut().find(|x| x.id == t.id) { let c = x.created_at; *x = t.clone(); x.created_at = c; }
                else { s.tasks.push(t.clone()); }
                if !want.is_empty() { id_map.insert(want, t.id); }
            }
        }
    }
    if let Some(arr) = b.get("progress").and_then(|v| v.as_array()) {
        for p in arr { if let Ok(mut p) = serde_json::from_value::<Progress>(p.clone()) {
            s.seq += 1; p.id = s.seq; if p.ts == 0 { p.ts = now_ms(); }
            if p.subject_kind == "task" { if let Some(real) = id_map.get(&p.subject_id) { p.subject_id = real.clone(); } }
            s.progress.push(p);
        }}
    }
    let counts = json!({"clusters":s.clusters.len(),"goals":s.goals.len(),"tasks":s.tasks.len(),"progress":s.progress.len()});
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":true,"counts":counts}))
}

/// 计划完成硬闸: 调 `omni plan-complete-gate <plan_id> --json`(隐藏窗口, 10 秒超时)。
/// 退出码 2 → Some(reason)(拒绝该 PATCH); 退出码 0 → None(放行);
/// 其余一切(超时/起进程失败/exit 1)→ None 但 eprintln 警告(fail-open: 闸管纪律不管安全,
/// 基建故障不许砸任务管理)。只在"非 done → done"且带 plan_id 时才被调用。
async fn check_plan_completion_gate(plan_id: &str) -> Option<String> {
    use tokio::process::Command as TokioCommand;

    let mut cmd = TokioCommand::new("cmd");
    cmd.arg("/C").arg("omni").arg("plan-complete-gate").arg(plan_id).arg("--json");
    #[cfg(windows)]
    cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW (tokio::process::Command 自带该方法)

    let run = tokio::time::timeout(std::time::Duration::from_secs(10), cmd.output()).await;
    let output = match run {
        Ok(Ok(o)) => o,
        Ok(Err(e)) => {
            eprintln!("[plan-complete-gate] 起进程失败, fail-open 放行 plan_id={}: {}", plan_id, e);
            return None;
        }
        Err(_) => {
            eprintln!("[plan-complete-gate] 10 秒超时, fail-open 放行 plan_id={}", plan_id);
            return None;
        }
    };

    match output.status.code() {
        Some(2) => {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let reason = serde_json::from_str::<Value>(&stdout)
                .ok()
                .and_then(|v| v.get("reason").and_then(|r| r.as_str()).map(|s| s.to_string()))
                .unwrap_or_else(|| format!("完成硬闸拒绝(plan_id={}), 原始输出: {}", plan_id, stdout.trim()));
            Some(reason)
        }
        Some(0) => None,
        other => {
            eprintln!(
                "[plan-complete-gate] 退出码异常({:?}), fail-open 放行 plan_id={}, stderr={}",
                other, plan_id, String::from_utf8_lossy(&output.stderr)
            );
            None
        }
    }
}

/// 完成硬闸是否要查：只对"计划级 task(无 parent_task_id)从非 done 变 done 且带 plan_id"触发。
/// 执行子任务(带 parent_task_id)做完一步不代表整计划完成，跳过（TASK-SSOT-UNIFICATION）。
fn gate_plan_for_done_patch(t: &Task, becomes_done: bool) -> Option<String> {
    if !becomes_done || t.parent_task_id.is_some() {
        return None;
    }
    match t.plan_id.as_ref() {
        Some(pid) if !pid.is_empty() => Some(pid.clone()),
        _ => None,
    }
}

/// 局部 patch 一条 task（只改给定字段，保留 goal_id 等其它字段）。omni-worker 推进用。
///
/// 完成硬闸(2026-07-04): 当 patch 把 status 从非 done 改为 done 类且任务带 plan_id 时,
/// 先调 check_plan_completion_gate 同步确认; 拒绝时返回 409 + reason JSON 且不落盘该 patch。
async fn patch_task(State(db): State<Db>, Json(b): Json<Value>) -> (StatusCode, Json<Value>) {
    let id = b.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if id.is_empty() { return (StatusCode::OK, Json(json!({"ok":false,"error":"missing id"}))); }

    // ── 完成硬闸前置检查(不持锁调外部进程, 避免长时间占用 store 互斥锁) ──
    if let Some(new_status) = b.get("status").and_then(|v| v.as_str()) {
        if is_done_status(new_status) {
            let gate_check = {
                let s = db.store.lock().unwrap();
                s.tasks.iter().find(|t| t.id == id).and_then(|t| {
                    gate_plan_for_done_patch(t, !is_done_status(&t.status))
                })
            };
            if let Some(plan_id) = gate_check {
                if let Some(reason) = check_plan_completion_gate(&plan_id).await {
                    return (
                        StatusCode::CONFLICT,
                        Json(json!({"ok": false, "error": "plan_completion_gate_refused", "plan_id": plan_id, "reason": reason})),
                    );
                }
            }
        }
    }

    let mut s = db.store.lock().unwrap();
    let mut found = false;
    if let Some(t) = s.tasks.iter_mut().find(|t| t.id == id) {
        if let Some(c) = b.get("completion").and_then(|v| v.as_i64()) { t.completion = c.clamp(0, 100) as i32; }
        if let Some(st) = b.get("status").and_then(|v| v.as_str()) { t.status = st.to_string(); }
        if let Some(lp) = b.get("latest_progress").and_then(|v| v.as_str()) { t.latest_progress = Some(lp.to_string()); }
        if let Some(g) = b.get("goal_id").and_then(|v| v.as_str()) { t.goal_id = Some(g.to_string()); }
        if let Some(l) = b.get("line").and_then(|v| v.as_str()) { t.line = l.to_string(); }
        t.updated_at = now_ms();
        found = true;
    }
    let snap = s.clone(); drop(s); db.save(&snap);
    (StatusCode::OK, Json(json!({"ok":found})))
}

/// 手动归档/取消归档一条 task。
async fn archive_task(State(db): State<Db>, Json(b): Json<Value>) -> Json<Value> {
    let id = b.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let archived = b.get("archived").and_then(|v| v.as_bool()).unwrap_or(true);
    let mut s = db.store.lock().unwrap();
    let mut found = false;
    if let Some(t) = s.tasks.iter_mut().find(|t| t.id == id) { t.archived = archived; t.updated_at = now_ms(); found = true; }
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":found}))
}

/// 自动归档规则（2026-06-24 用户：进度≥90% 的计划都结掉成归档内容）：
/// - 任务状态已完成/结单，或 completion ≥ 90 → 归档（不再占主列表，顶部勾选可看）。
/// - 置顶任务豁免：既然置顶了就别自动归档（置顶要真有效）。
/// - 执行子任务(带 parent_task_id)豁免：做完的执行步骤要留在计划下当履历，不自动消失
///   （TASK-SSOT-UNIFICATION）。
/// 返回归档了多少条。供定时任务与 /api/maintenance/auto-archive 调用。
fn auto_archive_core(app: &App) -> usize {
    let mut s = app.store.lock().unwrap();
    let pinned: std::collections::HashSet<String> = s.pins.iter()
        .filter(|p| p.subject_kind == "task").map(|p| p.subject_id.clone()).collect();
    let mut n = 0;
    for t in s.tasks.iter_mut() {
        if t.archived || pinned.contains(&t.id) || t.parent_task_id.is_some() { continue; }
        if is_done_status(&t.status) || t.completion >= 90 {
            t.archived = true;
            n += 1;
        }
    }
    let snap = s.clone(); drop(s); app.save(&snap);
    n
}

async fn auto_archive(State(db): State<Db>) -> Json<Value> {
    Json(json!({"ok":true,"archived": auto_archive_core(&db)}))
}

/// 清空（重灌时用）。
async fn reset(State(db): State<Db>) -> Json<Value> {
    let mut s = db.store.lock().unwrap();
    *s = Store::default();
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":true}))
}

// ── 外部渠道同步（shell 出 meegle / multica，Windows 走 cmd /C） ───────────────

fn run_cli(parts: &[&str]) -> Result<String, String> {
    // Windows: 必须 CREATE_NO_WINDOW, 否则 progressd(无 console 服务)spawn 的 cmd/meegle 会被
    // 分配一个前台黑窗一闪(用户 2026-07-01 反馈"打开时跳前台窗口")。0x0800_0000 = CREATE_NO_WINDOW。
    #[cfg(windows)]
    let out = {
        use std::os::windows::process::CommandExt;
        Command::new("cmd").arg("/C").args(parts).creation_flags(0x0800_0000).output()
    };
    #[cfg(not(windows))]
    let out = Command::new(parts[0]).args(&parts[1..]).output();
    match out {
        Ok(o) => {
            if o.status.success() || !o.stdout.is_empty() {
                Ok(String::from_utf8_lossy(&o.stdout).to_string())
            } else {
                Err(String::from_utf8_lossy(&o.stderr).to_string())
            }
        }
        Err(e) => Err(e.to_string()),
    }
}

/// 外部渠道条目批量灌入（feeder 用：Python 端取 meego/multica 原始数据、清洗后 POST 进来，
/// 统一身份去重在本地模型（Rust）这层做——同 external_ref 或同 BUG-key 不重复建单）。
async fn ingest_external(State(db): State<Db>, Json(b): Json<Value>) -> Json<Value> {
    let items = b.get("items").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let mut s = db.store.lock().unwrap();
    let before = s.tasks.len();
    let mut n = 0;
    for it in &items {
        let ext = it.get("external_ref").map(jstr).unwrap_or_default();
        if ext.is_empty() { continue; }
        upsert_external(&mut s, IncomingTask {
            title: it.get("title").map(jstr).filter(|x| !x.is_empty()).unwrap_or_else(|| ext.clone()),
            status: it.get("status").map(jstr).unwrap_or_default(),
            channel: it.get("channel").map(jstr).filter(|x| !x.is_empty()).unwrap_or_else(|| "meego".into()),
            external_ref: ext,
            assignee: it.get("assignee").map(jstr).filter(|x| !x.is_empty()),
            due_date: it.get("due_date").map(jstr).filter(|x| !x.is_empty()),
            completion: it.get("completion").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
        });
        n += 1;
    }
    let new_cnt = s.tasks.len() - before;
    let merged = n - new_cnt;
    let snap = s.clone(); drop(s); db.save(&snap);
    Json(json!({"ok":true,"ingested":n,"new":new_cnt,"merged_unified":merged}))
}

/// 同步 meego（经办人/负责人=周灏文 的 mywork todo，翻页拉全）→ 统一身份 upsert。
fn sync_meego_core(app: &App) -> Result<usize, String> {
    let mut total = 0;
    for page in 1..=10 {
        let p = page.to_string();
        let raw = run_cli(&["meegle", "mywork", "todo", "--action", "todo", "--page-num", &p, "--format", "json"])
            .map_err(|e| format!("meegle: {}", e))?;
        let v: Value = serde_json::from_str(&raw).map_err(|e| format!("parse p{}: {}", page, e))?;
        let items = extract_array(&v);
        if items.is_empty() { break; }
        {
            let mut s = app.store.lock().unwrap();
            for it in &items {
                let wi = it.get("work_item_info");
                let id = wi.and_then(|w| w.get("work_item_id")).or_else(|| it.get("id")).map(jstr).unwrap_or_default();
                if id.is_empty() { continue; }
                let title = wi.and_then(|w| w.get("work_item_name")).or_else(|| it.get("name")).map(jstr).unwrap_or_else(|| format!("meego {}", id));
                let status = it.get("state_info").and_then(|x| x.get("start_state_key_name")).map(jstr).filter(|x| !x.is_empty()).unwrap_or_else(|| "todo".into());
                let due = it.get("schedule").and_then(|x| x.get("end_time")).map(jstr).filter(|x| !x.is_empty());
                upsert_external(&mut s, IncomingTask {
                    title, status, channel: "meego".into(),
                    external_ref: format!("meego:{}", id),
                    assignee: Some("周灏文".into()), due_date: due, completion: 0,
                });
                total += 1;
            }
            let snap = s.clone(); drop(s); app.save(&snap);
        }
        if items.len() < 30 { break; } // 最后一页
    }
    Ok(total)
}

fn sync_multica_core(app: &App) -> Result<usize, String> {
    let raw = run_cli(&["multica", "issue", "list", "--output", "json"]).map_err(|e| format!("multica: {}", e))?;
    let v: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
    let items = extract_array(&v);
    let mut s = app.store.lock().unwrap();
    let mut n = 0;
    for it in &items {
        let id = it.get("key").or_else(|| it.get("id")).map(jstr).unwrap_or_default();
        if id.is_empty() { continue; }
        upsert_external(&mut s, IncomingTask {
            title: it.get("title").map(jstr).unwrap_or_else(|| id.clone()),
            status: it.get("status").map(jstr).unwrap_or_default(),
            channel: "multica".into(),
            external_ref: format!("multica:{}", id),
            assignee: it.get("assignee").map(jstr), due_date: None, completion: 0,
        });
        n += 1;
    }
    let snap = s.clone(); drop(s); app.save(&snap);
    Ok(n)
}

async fn sync_meego(State(db): State<Db>) -> (StatusCode, Json<Value>) {
    match sync_meego_core(&db) {
        Ok(n) => (StatusCode::OK, Json(json!({"ok":true,"synced":n,"channel":"meego"}))),
        Err(e) => (StatusCode::BAD_GATEWAY, Json(json!({"ok":false,"error":e}))),
    }
}
async fn sync_multica(State(db): State<Db>) -> (StatusCode, Json<Value>) {
    match sync_multica_core(&db) {
        Ok(n) => (StatusCode::OK, Json(json!({"ok":true,"synced":n,"channel":"multica"}))),
        Err(e) => (StatusCode::BAD_GATEWAY, Json(json!({"ok":false,"error":e}))),
    }
}

fn jstr(v: &Value) -> String {
    match v { Value::String(s) => s.clone(), Value::Null => String::new(), other => other.to_string() }
}
/// 从 CLI 各种 envelope 里挖出条目数组。
fn extract_array(v: &Value) -> Vec<Value> {
    if let Some(a) = v.as_array() { return a.clone(); }
    for k in ["data", "items", "issues", "work_items", "list", "results"] {
        if let Some(a) = v.get(k).and_then(|x| x.as_array()) { return a.clone(); }
        if let Some(inner) = v.get(k) { if let Some(a) = inner.as_array() { return a.clone(); } }
    }
    if let Some(d) = v.get("data") { return extract_array(d); }
    vec![]
}

/// 数据目录解析（防呆）：裸启动（不走 start-progress-service.cmd、无数据目录 env）时绝不能
/// 退回 cwd 新建空库——2026-07-02 曾因此让看板目标"全部消失"（真库在 data/，服务在仓根另起炉灶）。
/// 顺序：PROGRESS_SERVICE_DATA_DIR → WHATNOW_DATA_DIR(兼容) → cwd 下已有库的 data/ → exe 所在仓的 data/ → cwd。
fn resolve_data_dir() -> std::path::PathBuf {
    if let Ok(d) = std::env::var("PROGRESS_SERVICE_DATA_DIR") {
        return std::path::PathBuf::from(d);
    }
    if let Ok(d) = std::env::var("WHATNOW_DATA_DIR") {
        return std::path::PathBuf::from(d);
    }
    let cwd_data = std::path::Path::new("data");
    if cwd_data.join("whatnow.json").exists() {
        return cwd_data.to_path_buf();
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(repo) = exe.ancestors().nth(3) {
            let d = repo.join("data");
            if d.join("whatnow.json").exists() {
                return d;
            }
        }
    }
    std::path::PathBuf::from(".")
}

#[tokio::main]
async fn main() {
    let dir = resolve_data_dir();
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join("whatnow.json");
    let app_state: Db = Arc::new(App::load(path.clone()));
    eprintln!("[progress-service] data file: {}", path.display());

    let cors = CorsLayer::new().allow_origin(Any).allow_methods(Any).allow_headers(Any);
    let router = Router::new()
        .route("/health", get(health))
        .route("/api/board", get(board))
        .route("/api/plan-tasks", get(plan_tasks))
        .route("/api/clusters", post(upsert_cluster))
        .route("/api/goals", post(upsert_goal))
        .route("/api/tasks", post(upsert_task))
        .route("/api/progress", post(add_progress))
        .route("/api/pins", get(get_pins))
        .route("/api/pin", post(set_pin))
        .route("/api/focus", get(get_focus).post(set_focus)) // 兼容旧接口
        .route("/api/seed", post(seed))
        .route("/api/task/patch", post(patch_task))
        .route("/api/task/archive", post(archive_task))
        .route("/api/maintenance/auto-archive", post(auto_archive))
        .route("/api/reset", post(reset))
        .route("/api/sync/meego", post(sync_meego))
        .route("/api/sync/multica", post(sync_multica))
        .route("/api/ingest/external", post(ingest_external))
        .layer(cors)
        .with_state(app_state.clone());

    // 定时后台同步（R6：经办人/负责人=周灏文 的 meego 单子 + multica 议题，每 15 分钟拉一次；
    // 启动即先拉一次）。统一身份去重在 upsert_external 里做，跑多少次都不会重复建单。
    {
        let bg = app_state.clone();
        tokio::spawn(async move {
            let mut iv = tokio::time::interval(std::time::Duration::from_secs(900));
            loop {
                iv.tick().await;
                let a = bg.clone();
                let _ = tokio::task::spawn_blocking(move || {
                    if let Ok(n) = sync_multica_core(&a) { eprintln!("[progress-service] periodic multica: {}", n); }
                    if let Ok(n) = sync_meego_core(&a) { eprintln!("[progress-service] periodic meego: {}", n); }
                    let arch = auto_archive_core(&a);
                    if arch > 0 { eprintln!("[progress-service] auto-archived: {}", arch); }
                }).await;
            }
        });
    }

    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], PORT));
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind 8230");
    eprintln!("[progress-service] listening on http://{}", addr);
    axum::serve(listener, router).await.expect("serve");
}

// ── 单测（TASK-SSOT-UNIFICATION：执行字段 roundtrip / 老数据兼容 / 硬闸与归档豁免） ──

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_store_path(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("progressd-test-{}-{}", tag, now_ms()));
        let _ = std::fs::create_dir_all(&d);
        d.join("whatnow.json")
    }

    #[test]
    fn exec_fields_roundtrip() {
        let t = Task {
            id: "p_x.1".into(),
            parent_task_id: Some("p_x".into()),
            title: "写测试".into(),
            status: "pending".into(),
            plan_id: Some("cat/[2026-07-05]X".into()),
            details: "自包含细节".into(),
            test_strategy: "pytest 全绿".into(),
            dependencies: vec!["p_x.0".into()],
            priority: "high".into(),
            file_scope: vec!["a.py".into()],
            expected_outputs: vec!["a.py".into()],
            notes: vec![json!({"ts": 1.0, "text": "记一笔"})],
            complexity: Some(5),
            parallel: true,
            workload: Some(3),
            difficulty: Some(4),
            reasoning: "拆分理由".into(),
            ..Default::default()
        };
        let s = serde_json::to_string(&t).unwrap();
        let back: Task = serde_json::from_str(&s).unwrap();
        assert_eq!(back.details, "自包含细节");
        assert_eq!(back.test_strategy, "pytest 全绿");
        assert_eq!(back.dependencies, vec!["p_x.0".to_string()]);
        assert_eq!(back.priority, "high");
        assert_eq!(back.file_scope, vec!["a.py".to_string()]);
        assert_eq!(back.notes.len(), 1);
        assert_eq!(back.complexity, Some(5));
        assert!(back.parallel);
        assert_eq!(back.workload, Some(3));
        assert_eq!(back.reasoning, "拆分理由");
    }

    #[test]
    fn legacy_task_json_without_exec_fields_deserializes() {
        // 存量 whatnow.json 里的老任务没有任何执行字段 → serde default 全兜住
        let legacy = r#"{"id":"t103","title":"老单子","status":"todo","channel":"multica",
            "external_refs":["multica:abc"],"archived":false,"created_at":1,"updated_at":2}"#;
        let t: Task = serde_json::from_str(legacy).unwrap();
        assert!(t.details.is_empty() && t.dependencies.is_empty() && !t.parallel);
        // 序列化回去不应带出空执行字段（whatnow.json 不膨胀）
        let out = serde_json::to_string(&t).unwrap();
        assert!(!out.contains("test_strategy") && !out.contains("file_scope") && !out.contains("parallel"));
    }

    #[test]
    fn gate_skips_exec_subtasks() {
        let parent = Task { id: "p_x".into(), plan_id: Some("cat/X".into()), ..Default::default() };
        let child = Task { id: "p_x.1".into(), plan_id: Some("cat/X".into()),
            parent_task_id: Some("p_x".into()), ..Default::default() };
        assert_eq!(gate_plan_for_done_patch(&parent, true), Some("cat/X".into()));
        assert_eq!(gate_plan_for_done_patch(&child, true), None); // 子任务不触发硬闸
        assert_eq!(gate_plan_for_done_patch(&parent, false), None); // 已是 done 不再查
    }

    #[test]
    fn auto_archive_skips_exec_subtasks() {
        let app = App::load(tmp_store_path("aa"));
        {
            let mut s = app.store.lock().unwrap();
            s.tasks.push(Task { id: "p_x".into(), title: "计划".into(), status: "in_progress".into(),
                plan_id: Some("cat/X".into()), ..Default::default() });
            s.tasks.push(Task { id: "p_x.1".into(), title: "步骤".into(), status: "done".into(),
                parent_task_id: Some("p_x".into()), completion: 100, ..Default::default() });
            s.tasks.push(Task { id: "t1".into(), title: "普通已完成".into(), status: "done".into(),
                ..Default::default() });
        }
        let n = auto_archive_core(&app);
        assert_eq!(n, 1); // 只归档普通已完成的 t1
        let s = app.store.lock().unwrap();
        assert!(!s.tasks.iter().find(|t| t.id == "p_x.1").unwrap().archived);
        assert!(s.tasks.iter().find(|t| t.id == "t1").unwrap().archived);
    }
}
