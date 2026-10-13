import { useEffect, useRef, useState } from "react";
import { useList } from "@refinedev/core";
import { Alert, App, Button, Card, Col, DatePicker, Descriptions, Drawer, Form, Input, InputNumber, Modal, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { api } from "./api";

const money = (v: any) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const labels: Record<string, string> = { BANK: "银行", EQUITY: "权益", RECEIVABLE: "应收", PAYABLE: "应付", OPERATING_REVENUE: "营业收入", OPERATING_EXPENSE: "营业费用", NON_CURRENT_ASSET: "非流动资产", CURRENT_ASSET: "流动资产", CURRENT_LIABILITY: "流动负债", NON_CURRENT_LIABILITY: "非流动负债", DIRECT_EXPENSE: "直接费用", OVERHEAD_EXPENSE: "管理费用", journal: "日记账", suspense: "未知入账", reclassification: "重新分类", invoice: "客户发票", bill: "供应商账单", receipt: "客户收款", payment: "供应商付款" };
const resultLabels: Record<string, string> = { GROSS_PROFIT: "毛利润", TOTAL_REVENUE: "收入小计", TOTAL_EXPENSES: "费用小计", NET_PROFIT: "净利润", DEBIT: "借方合计", CREDIT: "贷方合计", NET_ASSETS: "净资产", TOTAL_ASSETS: "资产合计", TOTAL_LIABILITIES: "负债合计", TOTAL_EQUITY: "权益合计", NET_CASH_FLOW: "现金流净额" };
const today = () => dayjs().format("YYYY-MM-DD");
function useRows(resource: string) {
  const list = useList<any>({ resource, pagination: { mode: "off" } });
  return { rows: list.result.data || [], loading: list.query.isLoading, refresh: list.query.refetch };
}
function useUser() { const [user, set] = useState<any>({ role: "Viewer" }); useEffect(() => { api("users/me").then(set); }, []); return user; }
function useSubmit(refresh: () => any, close: () => void) {
  const { message } = App.useApp(); const [busy, set] = useState(false); const key = useRef(crypto.randomUUID());
  return { busy, submit: async (path: string, values: any, method = "POST") => {
    set(true); try { await api(path, { method, body: JSON.stringify(values), headers: { "Idempotency-Key": key.current } });
      key.current = crypto.randomUUID(); message.success("已保存"); close(); await refresh();
    } catch (e) { message.error((e as any).message); } finally { set(false); }
  } };
}
function Page({ title, extra, children }: any) { return <><div className="page-title"><Typography.Title level={3}>{title}</Typography.Title>{extra}</div>{children}</>; }
function AccountsSelect({ accounts, types, ...props }: any) { return <Select showSearch optionFilterProp="label" style={{ minWidth: 150 }} options={accounts.filter((a: any) => !types || types.includes(a.account_type)).map((a: any) => ({ value: a.id, label: `${a.account_code} · ${a.name}` }))} {...props} />; }
function DateField({ name = "date", label = "日期" }: any) { return <Form.Item name={name} label={label} rules={[{ required: true }]}><Input type="date" /></Form.Item>; }

export function Dashboard() {
  const { rows } = useRows("accounts"), tx = useRows("transactions"); const [company, set] = useState<any>({});
  useEffect(() => { api("company").then(set); }, []);
  return <Page title={company.name || "概览"} extra={<Tag color="blue">USD · 单账套</Tag>}>
    <Row gutter={[16, 16]}><Col xs={24} md={8}><Card><Statistic title="银行余额" value={rows.filter(a => a.account_type === "BANK").reduce((v, a) => v + Number(a.balance), 0)} precision={2} prefix="$" /></Card></Col>
      <Col xs={24} md={8}><Card><Statistic title="应收余额" value={rows.filter(a => a.account_type === "RECEIVABLE").reduce((v, a) => v + Number(a.balance), 0)} precision={2} prefix="$" /></Card></Col>
      <Col xs={24} md={8}><Card><Statistic title="待分类入账" value={tx.rows.filter(t => t.kind === "suspense" && !t.resolved).length} suffix="笔" /></Card></Col></Row>
    <Card title="最近交易" style={{ marginTop: 24 }}><Table rowKey="id" dataSource={tx.rows.slice(0, 8)} pagination={false} columns={[{ title: "日期", dataIndex: "date" }, { title: "说明", dataIndex: "description" }, { title: "类型", dataIndex: "kind", render: k => labels[k] || k }, { title: "金额", dataIndex: "amount", align: "right", render: money }]} /></Card>
  </Page>;
}

export function Accounts() {
  const data = useRows("accounts"), user = useUser(); const [open, set] = useState(false), [search, setSearch] = useState("");
  const [form] = Form.useForm(); const send = useSubmit(data.refresh, () => { set(false); form.resetFields(); });
  return <Page title="科目与账户" extra={user.role !== "Viewer" && <Button type="primary" onClick={() => set(true)}>新增科目</Button>}>
    <Card><Input.Search placeholder="搜索科目" allowClear onChange={e => setSearch(e.target.value)} style={{ width: 260, marginBottom: 16 }} />
      <Table rowKey="id" loading={data.loading} dataSource={data.rows.filter(a => a.name.toLowerCase().includes(search.toLowerCase()))} columns={[
        { title: "编号", dataIndex: "account_code" }, { title: "科目", dataIndex: "name" }, { title: "类别", dataIndex: "account_type", render: k => labels[k] || k }, { title: "借方净余额", dataIndex: "balance", align: "right", render: money }]} /></Card>
    <Modal title="新增科目" open={open} onCancel={() => set(false)} onOk={() => form.submit()} confirmLoading={send.busy}>
      <Form form={form} layout="vertical" onFinish={v => send.submit("accounts", v)}><Form.Item name="name" label="科目名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="account_type" label="类别" rules={[{ required: true }]}><Select options={Object.entries(labels).filter(([k]) => k === k.toUpperCase()).map(([value, label]) => ({ value, label }))} /></Form.Item></Form></Modal>
  </Page>;
}

export function Transactions() {
  const data = useRows("transactions"), accounts = useRows("accounts"), user = useUser();
  const [open, setOpen] = useState(false), [selected, setSelected] = useState<any>(), [kind, setKind] = useState("journal"), [filter, setFilter] = useState("all");
  const [range, setRange] = useState<any>(null); const [form] = Form.useForm();
  const [assignOpen, setAssignOpen] = useState(false), [assignForm] = Form.useForm();
  const send = useSubmit(() => { accounts.refresh(); return data.refresh(); }, () => { setOpen(false); form.resetFields(); });
  const allocate = useSubmit(data.refresh, () => { setAssignOpen(false); assignForm.resetFields(); });
  const writable = user.role !== "Viewer";
  const rows = data.rows.filter(t => (filter === "all" || t.kind === filter) && (!range || t.date >= range[0].format("YYYY-MM-DD") && t.date <= range[1].format("YYYY-MM-DD")));
  function create() { form.resetFields(); form.setFieldsValue({ date: today(), kind: "journal", lines: [{ quantity: 1 }] }); setKind("journal"); setOpen(true); }
  const docTypes = kind === "invoice" || kind === "receipt" ? ["RECEIVABLE"] : ["PAYABLE"];
  const lineTypes = kind === "receipt" || kind === "payment" ? ["BANK"] : kind === "invoice" ? ["OPERATING_REVENUE"] : undefined;
  return <Page title="交易与发票" extra={<Space>{writable && <><Button onClick={() => setAssignOpen(true)}>核销收付款</Button><Button type="primary" onClick={create}>新增交易</Button></>}</Space>}>
    <Card><Space wrap style={{ marginBottom: 16 }}><Select value={filter} onChange={setFilter} style={{ width: 160 }} options={[{ value: "all", label: "全部交易" }, ...["journal", "invoice", "bill", "receipt", "payment"].map(value => ({ value, label: labels[value] }))]} />
      <DatePicker.RangePicker onChange={setRange} /><Button onClick={() => window.open(`/api/export/transactions?start=${range ? range[0].format("YYYY-MM-DD") : dayjs().startOf("year").format("YYYY-MM-DD")}&end=${range ? range[1].format("YYYY-MM-DD") : today()}`)}>导出 CSV</Button></Space>
      <Table rowKey="id" loading={data.loading} dataSource={rows} columns={[{ title: "日期", dataIndex: "date" }, { title: "单号", dataIndex: "number" }, { title: "类型", dataIndex: "kind", render: k => labels[k] || k },
        { title: "说明", dataIndex: "description" }, { title: "金额", dataIndex: "amount", align: "right", render: money },
        { title: "未核销", render: (_, t) => ["invoice", "bill"].includes(t.kind) ? money(t.remaining) : "—" },
        { title: "", render: (_, t) => <Button type="link" onClick={() => setSelected(t)}>详情</Button> }]} /></Card>
    <Drawer title={selected?.number} open={!!selected} onClose={() => setSelected(undefined)} width={620}
      extra={selected?.kind === "journal" && !selected.reversed && writable && <Button danger onClick={() => Modal.confirm({ title: "冲销这笔日记账？", content: "将追加一笔反向分录，原始记录保留。", onOk: async () => { await api(`journals/${selected.id}/reverse`, { method: "POST", headers: { "Idempotency-Key": `reverse-journal-${selected.id}` }, body: JSON.stringify({ date: today(), description: `冲销 ${selected.number}` }) }); setSelected(undefined); data.refresh(); accounts.refresh(); } })}>冲销</Button>}>
      {selected && <><Descriptions column={1} items={[{ key: "date", label: "日期", children: selected.date }, { key: "description", label: "说明", children: selected.description }, { key: "account", label: "主科目", children: selected.account }, { key: "due", label: "到期日", children: selected.due_date || "—" }]} />
        <Table rowKey="account_id" dataSource={selected.lines} pagination={false} columns={[{ title: "科目", dataIndex: "account" }, { title: "说明", dataIndex: "description" }, { title: "单价", dataIndex: "amount", render: money }, { title: "数量", dataIndex: "quantity" }]} /></>}
    </Drawer>
    <Modal title="新增并过账" open={open} width={800} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={send.busy}>
      <Form form={form} layout="vertical" initialValues={{ date: today(), kind: "journal", lines: [{ quantity: 1 }] }} onFinish={v => {
        if (kind === "journal") send.submit("journals", { date: v.date, description: v.description, reference: v.reference || "", lines: [{ account_id: v.debit_id, debit: String(v.total) }, { account_id: v.credit_id, credit: String(v.total) }] });
        else send.submit("documents", { ...v, reference: v.reference || "", lines: v.lines.map((l: any) => ({ ...l, amount: String(l.amount), quantity: String(l.quantity || 1) })) });
      }}>
        <Form.Item name="kind" label="类型"><Select onChange={v => { form.resetFields(); form.setFieldsValue({ kind: v, date: today(), lines: [{ quantity: 1 }] }); setKind(v); }} options={["journal", "invoice", "bill", "receipt", "payment"].map(value => ({ value, label: labels[value] }))} /></Form.Item>
        <Row gutter={16}><Col span={12}><DateField /></Col><Col span={12}><Form.Item name="reference" label="参考号"><Input /></Form.Item></Col></Row>
        <Form.Item name="description" label="说明" rules={[{ required: true }]}><Input /></Form.Item>
        {kind === "journal" ? <><Row gutter={16}><Col span={12}><Form.Item name="debit_id" label="借方科目" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} /></Form.Item></Col>
          <Col span={12}><Form.Item name="credit_id" label="贷方科目" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} /></Form.Item></Col></Row><Form.Item name="total" label="金额" rules={[{ required: true }]}><InputNumber min={0.01} precision={2} /></Form.Item></> : <>
          <Form.Item name="account_id" label={docTypes[0] === "RECEIVABLE" ? "客户应收科目" : "供应商应付科目"} rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} types={docTypes} /></Form.Item>
          {["invoice", "bill"].includes(kind) && <Form.Item name="due_date" label="到期日"><Input type="date" /></Form.Item>}
          <Form.List name="lines">{(fields, { add, remove }) => <>{fields.map(field => <Row gutter={8} key={field.key}>
            <Col span={7}><Form.Item name={[field.name, "description"]} label="明细描述" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col span={7}><Form.Item name={[field.name, "account_id"]} label="科目" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} types={lineTypes} /></Form.Item></Col>
            <Col span={4}><Form.Item name={[field.name, "amount"]} label="单价" rules={[{ required: true }]}><InputNumber min={0.01} precision={2} style={{ width: "100%" }} /></Form.Item></Col>
            <Col span={4}><Form.Item name={[field.name, "quantity"]} label="数量"><InputNumber min={0.01} precision={2} style={{ width: "100%" }} /></Form.Item></Col>
            <Col span={2}><Button style={{ marginTop: 30 }} disabled={fields.length === 1} onClick={() => remove(field.name)}>×</Button></Col></Row>)}<Button onClick={() => add({ quantity: 1 })}>添加明细</Button></>}</Form.List>
        </>}
      </Form></Modal>
    <Modal title="核销收付款" open={assignOpen} onCancel={() => setAssignOpen(false)} onOk={() => assignForm.submit()} confirmLoading={allocate.busy}>
      <Form form={assignForm} layout="vertical" initialValues={{ date: today() }} onFinish={v => allocate.submit("assignments", { ...v, amount: String(v.amount) })}>
        <Form.Item name="payment_id" label="收款／付款" rules={[{ required: true }]}><Select options={data.rows.filter(t => ["receipt", "payment"].includes(t.kind)).map(t => ({ value: t.id, label: `${t.number} · ${t.account} · ${money(t.amount)}` }))} /></Form.Item>
        <Form.Item name="document_id" label="发票／账单" rules={[{ required: true }]}><Select options={data.rows.filter(t => ["invoice", "bill"].includes(t.kind) && Number(t.remaining) > 0).map(t => ({ value: t.id, label: `${t.number} · ${t.account} · 剩余 ${money(t.remaining)}` }))} /></Form.Item>
        <DateField /><Form.Item name="amount" label="核销金额" rules={[{ required: true }]}><InputNumber min={0.01} precision={2} /></Form.Item>
      </Form></Modal>
  </Page>;
}

export function Pending() {
  const data = useRows("transactions"), accounts = useRows("accounts"), user = useUser(); const [open, setOpen] = useState(false), [target, setTarget] = useState<any>();
  const [form] = Form.useForm(), [resolveForm] = Form.useForm();
  const refresh = () => { accounts.refresh(); return data.refresh(); };
  const create = useSubmit(refresh, () => { setOpen(false); form.resetFields(); }), resolve = useSubmit(refresh, () => { setTarget(undefined); resolveForm.resetFields(); });
  return <Page title="未知入账" extra={user.role !== "Viewer" && <Button type="primary" onClick={() => setOpen(true)}>录入未知入账</Button>}>
    <Alert type="info" showIcon message="先挂到权益类 Suspense，查明用途后重新分类。银行入账和原始记录会保留。" style={{ marginBottom: 20 }} />
    <Card><Table rowKey="id" dataSource={data.rows.filter(t => t.kind === "suspense")} columns={[{ title: "日期", dataIndex: "date" }, { title: "说明", dataIndex: "description" }, { title: "金额", dataIndex: "amount", render: money },
      { title: "状态", render: (_, t) => <Tag color={t.resolved ? "green" : "orange"}>{t.resolved ? "已分类" : "待处理"}</Tag> },
      { title: "", render: (_, t) => !t.resolved && user.role !== "Viewer" && <Button onClick={() => { resolveForm.setFieldsValue({ date: today() }); setTarget(t); }}>分类</Button> }]} /></Card>
    <Modal title="录入未知入账" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={create.busy}>
      <Form form={form} layout="vertical" initialValues={{ date: today() }} onFinish={v => create.submit("suspense", { ...v, amount: String(v.amount) })}>
        <Form.Item name="bank_id" label="银行" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} types={["BANK"]} /></Form.Item><DateField />
        <Form.Item name="description" label="说明" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="amount" label="金额" rules={[{ required: true }]}><InputNumber precision={2} min={0.01} /></Form.Item>
      </Form></Modal>
    <Modal title={`分类入账 · ${money(target?.amount)} USD`} open={!!target} onCancel={() => setTarget(undefined)} onOk={() => resolveForm.submit()} confirmLoading={resolve.busy}>
      <Form form={resolveForm} layout="vertical" onFinish={v => resolve.submit(`suspense/${target.id}/resolve`, v)}>
        <Form.Item name="account_id" label="实际科目" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows.filter(a => a.name !== "Suspense" && a.id !== target?.account_id)} /></Form.Item>
        <DateField /><Form.Item name="description" label="分类原因" rules={[{ required: true }]}><Input /></Form.Item>
      </Form></Modal>
  </Page>;
}

export function Reports() {
  const [name, setName] = useState("income"), [range, setRange] = useState<any>([dayjs().startOf("year"), dayjs()]), [report, setReport] = useState<any>(), [loading, setLoading] = useState(false);
  const { message } = App.useApp();
  const params = `start=${range[0].format("YYYY-MM-DD")}&end=${range[1].format("YYYY-MM-DD")}`;
  useEffect(() => { let active = true; setLoading(true); api(`reports/${name}?${params}`).then(r => { if (active) setReport(r); }).catch(e => { if (active) { setReport(undefined); message.error(e.message); } }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [name, params]);
  return <Page title="财务报表" extra={<Space><DatePicker.RangePicker allowClear={false} value={range} onChange={v => { if (v) setRange(v); }} /><Button onClick={() => window.open(`/api/export/reports/${name}?${params}`)}>导出 CSV</Button><Button onClick={() => window.print()}>打印</Button></Space>}>
    <Tabs activeKey={name} onChange={setName} items={[{ key: "income", label: "利润表" }, { key: "balance", label: "资产负债表" }, { key: "trial", label: "试算平衡表" }, { key: "cashflow", label: "现金流量表" }]} />
    <Card loading={loading}>{report && <><div className="report-totals">{Object.entries(report.results).map(([k, v]) => <Statistic key={k} title={resultLabels[k] || k.replaceAll("_", " ")} value={Number(v)} precision={2} />)}</div>
      <Table<any> className="report-print" rowKey={r => `${r.section}-${r.account_code}`} pagination={false} dataSource={report.rows} locale={{ emptyText: "所选日期内没有发生额" }} columns={[
        { title: "编号", dataIndex: "account_code" }, { title: "科目", dataIndex: "account" },
        { title: "分组", dataIndex: "category" }, { title: "借方净额（USD）", dataIndex: "amount", align: "right", render: money }]} />
      <Typography.Text type="secondary">科目明细以借方为正、贷方为负；上方汇总按报表口径显示。</Typography.Text>
    </>}</Card>
  </Page>;
}

export function Users() {
  const user = useUser(), [rows, setRows] = useState<any[]>([]), [open, setOpen] = useState(false), [form] = Form.useForm(); const { message } = App.useApp();
  const refresh = () => api("admin/users").then(setRows).catch(e => message.error(e.message));
  useEffect(() => { if (user.role === "Admin") refresh(); }, [user.role]);
  const send = useSubmit(refresh, () => { setOpen(false); form.resetFields(); });
  if (user.role !== "Admin") return <Alert message="仅管理员可以管理用户" type="info" />;
  return <Page title="用户与角色" extra={<Button type="primary" onClick={() => setOpen(true)}>新增用户</Button>}>
    <Card><Table rowKey="id" dataSource={rows} columns={[{ title: "邮箱", dataIndex: "email" }, { title: "角色", render: (_, u) => <Select value={u.role} disabled={u.id === user.id} options={["Admin", "Accountant", "Viewer"].map(value => ({ value, label: value }))} onChange={role => send.submit(`admin/users/${u.id}`, { role, is_active: u.is_active }, "PATCH")} /> },
      { title: "状态", render: (_, u) => <Button disabled={u.id === user.id} onClick={() => send.submit(`admin/users/${u.id}`, { role: u.role, is_active: !u.is_active }, "PATCH")}>{u.is_active ? "启用中 · 点击停用" : "已停用 · 点击启用"}</Button> }]} /></Card>
    <Modal title="新增用户" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={send.busy}><Form form={form} layout="vertical" initialValues={{ role: "Viewer" }} onFinish={v => send.submit("admin/users", v)}>
      <Form.Item name="email" label="邮箱" rules={[{ required: true, type: "email" }]}><Input /></Form.Item><Form.Item name="password" label="初始密码" rules={[{ required: true, min: 12 }]}><Input.Password autoComplete="new-password" /></Form.Item>
      <Form.Item name="role" label="角色"><Select options={["Admin", "Accountant", "Viewer"].map(value => ({ value, label: value }))} /></Form.Item></Form></Modal>
  </Page>;
}

export function Password() {
  const { message } = App.useApp(); const [busy, setBusy] = useState(false);
  return <Page title="修改密码"><Card style={{ maxWidth: 500 }}><Form layout="vertical" onFinish={async v => { setBusy(true); try { await api("users/password", { method: "POST", body: JSON.stringify(v) }); window.location.href = "/login"; } catch (e) { message.error((e as any).message); } finally { setBusy(false); } }}>
    <Form.Item name="current_password" label="当前密码" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
    <Form.Item name="password" label="新密码" rules={[{ required: true, min: 12 }]}><Input.Password autoComplete="new-password" /></Form.Item><Button type="primary" htmlType="submit" loading={busy}>保存并重新登录</Button>
  </Form></Card></Page>;
}
