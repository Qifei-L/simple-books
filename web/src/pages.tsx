import { useEffect, useRef, useState } from "react";
import { useList } from "@refinedev/core";
import { Alert, App, Button, Card, Col, DatePicker, Descriptions, Drawer, Form, Input, InputNumber, Modal, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { api } from "./api";

const money = (v: any) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const labels: Record<string, string> = { BANK: "Bank", EQUITY: "Equity", RECEIVABLE: "Receivables", PAYABLE: "Payables", OPERATING_REVENUE: "Operating revenue", OPERATING_EXPENSE: "Operating expenses", NON_CURRENT_ASSET: "Non-current assets", CURRENT_ASSET: "Current assets", CURRENT_LIABILITY: "Current liabilities", NON_CURRENT_LIABILITY: "Non-current liabilities", DIRECT_EXPENSE: "Direct expenses", OVERHEAD_EXPENSE: "Overhead expenses", journal: "Journal entry", suspense: "Unclassified Deposits", reclassification: "Reclassification", invoice: "Customer invoice", bill: "Supplier bill", receipt: "Customer receipt", payment: "Supplier payment" };
const resultLabels: Record<string, string> = { GROSS_PROFIT: "Gross profit", TOTAL_REVENUE: "Revenue subtotal", TOTAL_EXPENSES: "Expense subtotal", NET_PROFIT: "Net profit", DEBIT: "Total debits", CREDIT: "Total credits", NET_ASSETS: "Net assets", TOTAL_ASSETS: "Total assets", TOTAL_LIABILITIES: "Total liabilities", TOTAL_EQUITY: "Total equity", NET_CASH_FLOW: "Net cash flow" };
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
      key.current = crypto.randomUUID(); message.success("Saved"); close(); await refresh();
    } catch (e) { message.error((e as any).message); } finally { set(false); }
  } };
}
function Page({ title, extra, children }: any) { return <><div className="page-title"><Typography.Title level={3}>{title}</Typography.Title>{extra}</div>{children}</>; }
function AccountsSelect({ accounts, types, ...props }: any) { return <Select showSearch optionFilterProp="label" style={{ minWidth: 150 }} options={accounts.filter((a: any) => !types || types.includes(a.account_type)).map((a: any) => ({ value: a.id, label: `${a.account_code} · ${a.name}` }))} {...props} />; }
function DateField({ name = "date", label = "Date" }: any) { return <Form.Item name={name} label={label} rules={[{ required: true }]}><Input type="date" /></Form.Item>; }

export function Dashboard() {
  const { rows } = useRows("accounts"), tx = useRows("transactions"); const [company, set] = useState<any>({});
  useEffect(() => { api("company").then(set); }, []);
  return <Page title={company.name || "Overview"} extra={<Tag color="blue">USD · Single company</Tag>}>
    <Row gutter={[16, 16]}><Col xs={24} md={8}><Card><Statistic title="Bank balance" value={rows.filter(a => a.account_type === "BANK").reduce((v, a) => v + Number(a.balance), 0)} precision={2} prefix="$" /></Card></Col>
      <Col xs={24} md={8}><Card><Statistic title="Receivables balance" value={rows.filter(a => a.account_type === "RECEIVABLE").reduce((v, a) => v + Number(a.balance), 0)} precision={2} prefix="$" /></Card></Col>
      <Col xs={24} md={8}><Card><Statistic title="Unclassified deposits" value={tx.rows.filter(t => t.kind === "suspense" && !t.resolved).length} suffix="deposits" /></Card></Col></Row>
    <Card title="Recent Transactions" style={{ marginTop: 24 }}><Table rowKey="id" dataSource={tx.rows.slice(0, 8)} pagination={false} columns={[{ title: "Date", dataIndex: "date" }, { title: "Description", dataIndex: "description" }, { title: "Type", dataIndex: "kind", render: k => labels[k] || k }, { title: "Amount", dataIndex: "amount", align: "right", render: money }]} /></Card>
  </Page>;
}

export function Accounts() {
  const data = useRows("accounts"), user = useUser(); const [open, set] = useState(false), [search, setSearch] = useState("");
  const [form] = Form.useForm(); const send = useSubmit(data.refresh, () => { set(false); form.resetFields(); });
  return <Page title="Chart of Accounts" extra={user.role !== "Viewer" && <Button type="primary" onClick={() => set(true)}>Add Account</Button>}>
    <Card><Input.Search placeholder="Search accounts" allowClear onChange={e => setSearch(e.target.value)} style={{ width: 260, marginBottom: 16 }} />
      <Table rowKey="id" loading={data.loading} dataSource={data.rows.filter(a => a.name.toLowerCase().includes(search.toLowerCase()))} columns={[
        { title: "Code", dataIndex: "account_code" }, { title: "Account", dataIndex: "name" }, { title: "Category", dataIndex: "account_type", render: k => labels[k] || k }, { title: "Net debit balance", dataIndex: "balance", align: "right", render: money }]} /></Card>
    <Modal title="Add Account" open={open} onCancel={() => set(false)} onOk={() => form.submit()} confirmLoading={send.busy}>
      <Form form={form} layout="vertical" onFinish={v => send.submit("accounts", v)}><Form.Item name="name" label="Account name" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="account_type" label="Category" rules={[{ required: true }]}><Select options={Object.entries(labels).filter(([k]) => k === k.toUpperCase()).map(([value, label]) => ({ value, label }))} /></Form.Item></Form></Modal>
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
  return <Page title="Transactions & Invoices" extra={<Space>{writable && <><Button onClick={() => setAssignOpen(true)}>Allocate Payments</Button><Button type="primary" onClick={create}>Add Transaction</Button></>}</Space>}>
    <Card><Space wrap style={{ marginBottom: 16 }}><Select value={filter} onChange={setFilter} style={{ width: 160 }} options={[{ value: "all", label: "All transactions" }, ...["journal", "invoice", "bill", "receipt", "payment"].map(value => ({ value, label: labels[value] }))]} />
      <DatePicker.RangePicker onChange={setRange} /><Button onClick={() => window.open(`/api/export/transactions?start=${range ? range[0].format("YYYY-MM-DD") : dayjs().startOf("year").format("YYYY-MM-DD")}&end=${range ? range[1].format("YYYY-MM-DD") : today()}`)}>Export CSV</Button></Space>
      <Table rowKey="id" loading={data.loading} dataSource={rows} columns={[{ title: "Date", dataIndex: "date" }, { title: "Number", dataIndex: "number" }, { title: "Type", dataIndex: "kind", render: k => labels[k] || k },
        { title: "Description", dataIndex: "description" }, { title: "Amount", dataIndex: "amount", align: "right", render: money },
        { title: "Outstanding", render: (_, t) => ["invoice", "bill"].includes(t.kind) ? money(t.remaining) : "—" },
        { title: "", render: (_, t) => <Button type="link" onClick={() => setSelected(t)}>Details</Button> }]} /></Card>
    <Drawer title={selected?.number} open={!!selected} onClose={() => setSelected(undefined)} width={620}
      extra={selected?.kind === "journal" && !selected.reversed && writable && <Button danger onClick={() => Modal.confirm({ title: "Reverse this journal entry?", content: "A reversing entry will be added. The original record will be retained.", onOk: async () => { await api(`journals/${selected.id}/reverse`, { method: "POST", headers: { "Idempotency-Key": `reverse-journal-${selected.id}` }, body: JSON.stringify({ date: today(), description: `Reverse ${selected.number}` }) }); setSelected(undefined); data.refresh(); accounts.refresh(); } })}>Reverse</Button>}>
      {selected && <><Descriptions column={1} items={[{ key: "date", label: "Date", children: selected.date }, { key: "description", label: "Description", children: selected.description }, { key: "account", label: "Main account", children: selected.account }, { key: "due", label: "Due date", children: selected.due_date || "—" }]} />
        <Table rowKey="account_id" dataSource={selected.lines} pagination={false} columns={[{ title: "Account", dataIndex: "account" }, { title: "Description", dataIndex: "description" }, { title: "Unit price", dataIndex: "amount", render: money }, { title: "Quantity", dataIndex: "quantity" }]} /></>}
    </Drawer>
    <Modal title="Create and Post" open={open} width={800} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={send.busy}>
      <Form form={form} layout="vertical" initialValues={{ date: today(), kind: "journal", lines: [{ quantity: 1 }] }} onFinish={v => {
        if (kind === "journal") send.submit("journals", { date: v.date, description: v.description, reference: v.reference || "", lines: [{ account_id: v.debit_id, debit: String(v.total) }, { account_id: v.credit_id, credit: String(v.total) }] });
        else send.submit("documents", { ...v, reference: v.reference || "", lines: v.lines.map((l: any) => ({ ...l, amount: String(l.amount), quantity: String(l.quantity || 1) })) });
      }}>
        <Form.Item name="kind" label="Type"><Select onChange={v => { form.resetFields(); form.setFieldsValue({ kind: v, date: today(), lines: [{ quantity: 1 }] }); setKind(v); }} options={["journal", "invoice", "bill", "receipt", "payment"].map(value => ({ value, label: labels[value] }))} /></Form.Item>
        <Row gutter={16}><Col span={12}><DateField /></Col><Col span={12}><Form.Item name="reference" label="Reference"><Input /></Form.Item></Col></Row>
        <Form.Item name="description" label="Description" rules={[{ required: true }]}><Input /></Form.Item>
        {kind === "journal" ? <><Row gutter={16}><Col span={12}><Form.Item name="debit_id" label="Debit account" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} /></Form.Item></Col>
          <Col span={12}><Form.Item name="credit_id" label="Credit account" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} /></Form.Item></Col></Row><Form.Item name="total" label="Amount" rules={[{ required: true }]}><InputNumber min={0.01} precision={2} /></Form.Item></> : <>
          <Form.Item name="account_id" label={docTypes[0] === "RECEIVABLE" ? "Customer receivable account" : "Supplier payable account"} rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} types={docTypes} /></Form.Item>
          {["invoice", "bill"].includes(kind) && <Form.Item name="due_date" label="Due date"><Input type="date" /></Form.Item>}
          <Form.List name="lines">{(fields, { add, remove }) => <>{fields.map(field => <Row gutter={8} key={field.key}>
            <Col span={7}><Form.Item name={[field.name, "description"]} label="Line description" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col span={7}><Form.Item name={[field.name, "account_id"]} label="Account" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} types={lineTypes} /></Form.Item></Col>
            <Col span={4}><Form.Item name={[field.name, "amount"]} label="Unit price" rules={[{ required: true }]}><InputNumber min={0.01} precision={2} style={{ width: "100%" }} /></Form.Item></Col>
            <Col span={4}><Form.Item name={[field.name, "quantity"]} label="Quantity"><InputNumber min={0.01} precision={2} style={{ width: "100%" }} /></Form.Item></Col>
            <Col span={2}><Button style={{ marginTop: 30 }} disabled={fields.length === 1} onClick={() => remove(field.name)}>×</Button></Col></Row>)}<Button onClick={() => add({ quantity: 1 })}>Add Line</Button></>}</Form.List>
        </>}
      </Form></Modal>
    <Modal title="Allocate Payments" open={assignOpen} onCancel={() => setAssignOpen(false)} onOk={() => assignForm.submit()} confirmLoading={allocate.busy}>
      <Form form={assignForm} layout="vertical" initialValues={{ date: today() }} onFinish={v => allocate.submit("assignments", { ...v, amount: String(v.amount) })}>
        <Form.Item name="payment_id" label="Receipt / payment" rules={[{ required: true }]}><Select options={data.rows.filter(t => ["receipt", "payment"].includes(t.kind)).map(t => ({ value: t.id, label: `${t.number} · ${t.account} · ${money(t.amount)}` }))} /></Form.Item>
        <Form.Item name="document_id" label="Invoice / bill" rules={[{ required: true }]}><Select options={data.rows.filter(t => ["invoice", "bill"].includes(t.kind) && Number(t.remaining) > 0).map(t => ({ value: t.id, label: `${t.number} · ${t.account} · Outstanding ${money(t.remaining)}` }))} /></Form.Item>
        <DateField /><Form.Item name="amount" label="Allocation amount" rules={[{ required: true }]}><InputNumber min={0.01} precision={2} /></Form.Item>
      </Form></Modal>
  </Page>;
}

export function Pending() {
  const data = useRows("transactions"), accounts = useRows("accounts"), user = useUser(); const [open, setOpen] = useState(false), [target, setTarget] = useState<any>();
  const [form] = Form.useForm(), [resolveForm] = Form.useForm();
  const refresh = () => { accounts.refresh(); return data.refresh(); };
  const create = useSubmit(refresh, () => { setOpen(false); form.resetFields(); }), resolve = useSubmit(refresh, () => { setTarget(undefined); resolveForm.resetFields(); });
  return <Page title="Unclassified Deposits" extra={user.role !== "Viewer" && <Button type="primary" onClick={() => setOpen(true)}>Add Unclassified Deposit</Button>}>
    <Alert type="info" showIcon message="Post to the equity Suspense account first, then reclassify once identified. The bank deposit and original record are retained." style={{ marginBottom: 20 }} />
    <Card><Table rowKey="id" dataSource={data.rows.filter(t => t.kind === "suspense")} columns={[{ title: "Date", dataIndex: "date" }, { title: "Description", dataIndex: "description" }, { title: "Amount", dataIndex: "amount", render: money },
      { title: "Status", render: (_, t) => <Tag color={t.resolved ? "green" : "orange"}>{t.resolved ? "Classified" : "Pending"}</Tag> },
      { title: "", render: (_, t) => !t.resolved && user.role !== "Viewer" && <Button onClick={() => { resolveForm.setFieldsValue({ date: today() }); setTarget(t); }}>Classify</Button> }]} /></Card>
    <Modal title="Add Unclassified Deposit" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={create.busy}>
      <Form form={form} layout="vertical" initialValues={{ date: today() }} onFinish={v => create.submit("suspense", { ...v, amount: String(v.amount) })}>
        <Form.Item name="bank_id" label="Bank" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows} types={["BANK"]} /></Form.Item><DateField />
        <Form.Item name="description" label="Description" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="amount" label="Amount" rules={[{ required: true }]}><InputNumber precision={2} min={0.01} /></Form.Item>
      </Form></Modal>
    <Modal title={`Classify Deposit · ${money(target?.amount)} USD`} open={!!target} onCancel={() => setTarget(undefined)} onOk={() => resolveForm.submit()} confirmLoading={resolve.busy}>
      <Form form={resolveForm} layout="vertical" onFinish={v => resolve.submit(`suspense/${target.id}/resolve`, v)}>
        <Form.Item name="account_id" label="Target account" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows.filter(a => a.name !== "Suspense" && a.id !== target?.account_id)} /></Form.Item>
        <DateField /><Form.Item name="description" label="Reason for classification" rules={[{ required: true }]}><Input /></Form.Item>
      </Form></Modal>
  </Page>;
}

export function Reports() {
  const [name, setName] = useState("income"), [range, setRange] = useState<any>([dayjs().startOf("year"), dayjs()]), [report, setReport] = useState<any>(), [loading, setLoading] = useState(false);
  const { message } = App.useApp();
  const params = `start=${range[0].format("YYYY-MM-DD")}&end=${range[1].format("YYYY-MM-DD")}`;
  useEffect(() => { let active = true; setLoading(true); api(`reports/${name}?${params}`).then(r => { if (active) setReport(r); }).catch(e => { if (active) { setReport(undefined); message.error(e.message); } }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [name, params]);
  return <Page title="Financial Reports" extra={<Space><DatePicker.RangePicker allowClear={false} value={range} onChange={v => { if (v) setRange(v); }} /><Button onClick={() => window.open(`/api/export/reports/${name}?${params}`)}>Export CSV</Button><Button onClick={() => window.print()}>Print</Button></Space>}>
    <Tabs activeKey={name} onChange={setName} items={[{ key: "income", label: "Income Statement" }, { key: "balance", label: "Balance Sheet" }, { key: "trial", label: "Trial Balance" }, { key: "cashflow", label: "Cash Flow Statement" }]} />
    <Card loading={loading}>{report && <><div className="report-totals">{Object.entries(report.results).map(([k, v]) => <Statistic key={k} title={resultLabels[k] || k.replaceAll("_", " ")} value={Number(v)} precision={2} />)}</div>
      <Table<any> className="report-print" rowKey={r => `${r.section}-${r.account_code}`} pagination={false} dataSource={report.rows} locale={{ emptyText: "No activity in the selected period" }} columns={[
        { title: "Code", dataIndex: "account_code" }, { title: "Account", dataIndex: "account" },
        { title: "Group", dataIndex: "category" }, { title: "Net debit (USD)", dataIndex: "amount", align: "right", render: money }]} />
      <Typography.Text type="secondary">Account details show debits as positive and credits as negative. Summary figures follow the report presentation.</Typography.Text>
    </>}</Card>
  </Page>;
}

export function Users() {
  const user = useUser(), [rows, setRows] = useState<any[]>([]), [open, setOpen] = useState(false), [form] = Form.useForm(); const { message } = App.useApp();
  const refresh = () => api("admin/users").then(setRows).catch(e => message.error(e.message));
  useEffect(() => { if (user.role === "Admin") refresh(); }, [user.role]);
  const send = useSubmit(refresh, () => { setOpen(false); form.resetFields(); });
  if (user.role !== "Admin") return <Alert message="Only administrators can manage users." type="info" />;
  return <Page title="Users & Roles" extra={<Button type="primary" onClick={() => setOpen(true)}>Add User</Button>}>
    <Card><Table rowKey="id" dataSource={rows} columns={[{ title: "Email", dataIndex: "email" }, { title: "Role", render: (_, u) => <Select value={u.role} disabled={u.id === user.id} options={["Admin", "Accountant", "Viewer"].map(value => ({ value, label: value }))} onChange={role => send.submit(`admin/users/${u.id}`, { role, is_active: u.is_active }, "PATCH")} /> },
      { title: "Status", render: (_, u) => <Button disabled={u.id === user.id} onClick={() => send.submit(`admin/users/${u.id}`, { role: u.role, is_active: !u.is_active }, "PATCH")}>{u.is_active ? "Active · Disable" : "Disabled · Enable"}</Button> }]} /></Card>
    <Modal title="Add User" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={send.busy}><Form form={form} layout="vertical" initialValues={{ role: "Viewer" }} onFinish={v => send.submit("admin/users", v)}>
      <Form.Item name="email" label="Email" rules={[{ required: true, type: "email" }]}><Input /></Form.Item><Form.Item name="password" label="Initial password" rules={[{ required: true, min: 12 }]}><Input.Password autoComplete="new-password" /></Form.Item>
      <Form.Item name="role" label="Role"><Select options={["Admin", "Accountant", "Viewer"].map(value => ({ value, label: value }))} /></Form.Item></Form></Modal>
  </Page>;
}

export function Password() {
  const { message } = App.useApp(); const [busy, setBusy] = useState(false);
  return <Page title="Change Password"><Card style={{ maxWidth: 500 }}><Form layout="vertical" onFinish={async v => { setBusy(true); try { await api("users/password", { method: "POST", body: JSON.stringify(v) }); window.location.href = "/login"; } catch (e) { message.error((e as any).message); } finally { setBusy(false); } }}>
    <Form.Item name="current_password" label="Current password" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
    <Form.Item name="password" label="New password" rules={[{ required: true, min: 12 }]}><Input.Password autoComplete="new-password" /></Form.Item><Button type="primary" htmlType="submit" loading={busy}>Save and Sign In Again</Button>
  </Form></Card></Page>;
}
