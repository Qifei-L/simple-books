import { useEffect, useRef, useState } from "react";
import { useList } from "@refinedev/core";
import { Alert, App, Button, Card, Col, DatePicker, Descriptions, Drawer, Form, Input, InputNumber, Modal, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { api } from "./api";

const money = (v: any) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const labels: Record<string, string> = { BANK: "Bank", EQUITY: "Equity", RECEIVABLE: "Receivables", PAYABLE: "Payables", OPERATING_REVENUE: "Operating revenue", OPERATING_EXPENSE: "Operating expenses", NON_CURRENT_ASSET: "Non-current assets", CURRENT_ASSET: "Current assets", CURRENT_LIABILITY: "Current liabilities", NON_CURRENT_LIABILITY: "Non-current liabilities", DIRECT_EXPENSE: "Direct expenses", OVERHEAD_EXPENSE: "Overhead expenses", journal: "Journal entry", reversal: "Reversal", suspense: "Unclassified transaction", bank: "Bank transaction", reclassification: "Reclassification", invoice: "Customer invoice", bill: "Supplier bill", receipt: "Customer receipt", payment: "Supplier payment" };
const resultLabels: Record<string, string> = { GROSS_PROFIT: "Gross profit", TOTAL_REVENUE: "Total revenue", TOTAL_EXPENSES: "Total expenses", NET_PROFIT: "Net profit", DEBIT: "Total debits", CREDIT: "Total credits", NET_ASSETS: "Net assets", TOTAL_ASSETS: "Total assets", TOTAL_LIABILITIES: "Total liabilities", TOTAL_EQUITY: "Total equity", NET_CASH_FLOW: "Net cash flow", BEGIN_CASH_BALANCE: "Beginning cash balance", END_CASH_BALANCE: "Ending cash balance", CASHBOOK_BALANCE: "Cashbook balance" };
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
      <Col xs={24} md={8}><Card><Statistic title="Unclassified transactions" value={tx.rows.filter(t => t.kind === "suspense" && !t.resolved).length} suffix="transactions" /></Card></Col></Row>
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

type TransactionSection = "journal" | "sales" | "purchases" | "banking";
const sectionConfig = {
  journal: { title: "Journal", kinds: ["journal", "reversal"], create: ["journal"], payment: "", document: "", add: "New Journal Entry" },
  sales: { title: "Sales", kinds: ["invoice", "receipt"], create: ["invoice"], payment: "receipt", document: "invoice", add: "New Invoice" },
  purchases: { title: "Purchases", kinds: ["bill", "payment"], create: ["bill"], payment: "payment", document: "bill", add: "New Bill" },
  banking: { title: "Banking", kinds: ["journal", "reversal", "receipt", "payment", "suspense", "reclassification"], create: [], payment: "", document: "", add: "" },
};
function exportTransactions(rows: any[], section: string, fields = ["date", "number", "kind", "description", "account", "amount"]) {
  const escape = (v: any) => { let text = String(v ?? ""); if (/^[\s]*[=+@-]/.test(text)) text = "'" + text; return '"' + text.replaceAll('"', '""') + '"'; };
  const csv = '\ufeff' + [fields, ...rows.map(r => fields.map(f => r[f]))].map(r => r.map(escape).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = `${section}.csv`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function Transactions({ section = "journal" }: { section?: TransactionSection }) {
  const config = sectionConfig[section];
  const data = useRows("transactions"), accounts = useRows("accounts"), user = useUser();
  const [open, setOpen] = useState(false), [selected, setSelected] = useState<any>(), [kind, setKind] = useState<string>(config.create[0] || "journal"), [filter, setFilter] = useState("all");
  const [range, setRange] = useState<any>(null); const [form] = Form.useForm();
  const [assignOpen, setAssignOpen] = useState(false), [assignForm] = Form.useForm();
  const clearingId = Form.useWatch("payment_id", assignForm);
  const clearingSource = data.rows.find(t => t.id === clearingId);
  const side = section === "sales" ? "ar" : "ap";
  const clearTitle = section === "sales" ? "Clear A/R" : "Clear A/P";
  const send = useSubmit(() => { accounts.refresh(); return data.refresh(); }, () => { setOpen(false); form.resetFields(); });
  const allocate = useSubmit(data.refresh, () => { setAssignOpen(false); assignForm.resetFields(); });
  const writable = user.role !== "Viewer";
  const bankIds = new Set(accounts.rows.filter(a => a.account_type === "BANK").map(a => a.id));
  const scopedRows = data.rows.filter(t => section === "banking" ? bankIds.has(t.account_id) || t.lines.some((l: any) => bankIds.has(l.account_id)) : ((config.kinds as string[]).includes(t.kind) || (!!config.payment && t.settlement_side === side)));
  const rows = scopedRows.filter(t => (filter === "all" || t.kind === filter) && (!range || t.date >= range[0].format("YYYY-MM-DD") && t.date <= range[1].format("YYYY-MM-DD")));
  function create() { form.resetFields(); form.setFieldsValue({ date: today(), kind: config.create[0], lines: [{ quantity: 1 }] }); setKind(config.create[0]); setOpen(true); }
  const docTypes = kind === "invoice" || kind === "receipt" ? ["RECEIVABLE"] : ["PAYABLE"];
  const lineTypes = kind === "receipt" || kind === "payment" ? ["BANK"] : kind === "invoice" ? ["OPERATING_REVENUE"] : undefined;
  return <Page title={config.title} extra={<Space>{writable && <>{config.payment && <Button onClick={() => { assignForm.resetFields(); assignForm.setFieldsValue({ date: today() }); setAssignOpen(true); }}>{clearTitle}</Button>}{config.create.length > 0 && <Button type="primary" onClick={create}>{config.add}</Button>}</>}</Space>}>
    <Card><Space wrap style={{ marginBottom: 16 }}><Select value={filter} onChange={setFilter} style={{ width: 160 }} options={[{ value: "all", label: "All transactions" }, ...[...new Set([...config.kinds, ...scopedRows.map(t => t.kind)])].map(value => ({ value, label: labels[value] }))]} />
      <DatePicker.RangePicker onChange={setRange} /><Button onClick={() => exportTransactions(rows, section)}>Export CSV</Button></Space>
      <Table rowKey="id" loading={data.loading} dataSource={rows} columns={[{ title: "Date", dataIndex: "date" }, { title: "Number", dataIndex: "number" }, { title: "Type", dataIndex: "kind", render: k => labels[k] || k },
        { title: "Description", dataIndex: "description" }, { title: "Amount", dataIndex: "amount", align: "right", render: money },
        { title: "Outstanding / available", render: (_, t) => ["invoice", "bill"].includes(t.kind) ? money(t.remaining) : t.settlement_side ? money(t.available) : "—" },
        { title: "Status", render: (_, t) => ["invoice", "bill"].includes(t.kind) ? <Tag color={Number(t.remaining) === 0 ? "green" : Number(t.remaining) < Number(t.amount) ? "blue" : "orange"}>{Number(t.remaining) === 0 ? "Cleared" : Number(t.remaining) < Number(t.amount) ? "Partially Cleared" : "Open"}</Tag> : t.settlement_side ? <Tag>{Number(t.available) === 0 ? "Fully Applied" : "Available"}</Tag> : null },
        { title: "", render: (_, t) => <Button type="link" onClick={() => setSelected(t)}>Details</Button> }]} /></Card>
    <Drawer title={selected?.number} open={!!selected} onClose={() => setSelected(undefined)} width={620}
      extra={selected?.kind === "journal" && !selected.reversed && writable && <Button danger onClick={() => Modal.confirm({ title: "Reverse this journal entry?", content: "A reversing entry will be added. The original record will be retained.", onOk: async () => { await api(`journals/${selected.id}/reverse`, { method: "POST", headers: { "Idempotency-Key": `reverse-journal-${selected.id}` }, body: JSON.stringify({ date: today(), description: `Reverse ${selected.number}` }) }); setSelected(undefined); data.refresh(); accounts.refresh(); } })}>Reverse</Button>}>
      {selected && <><Descriptions column={1} items={[{ key: "date", label: "Date", children: selected.date }, { key: "description", label: "Description", children: selected.description }, { key: "account", label: "Main account", children: selected.account }, { key: "due", label: "Due date", children: selected.due_date || "—" }]} />
        <Table rowKey="account_id" dataSource={selected.lines} pagination={false} columns={[{ title: "Account", dataIndex: "account" }, { title: "Description", dataIndex: "description" }, { title: "Unit price", dataIndex: "amount", render: money }, { title: "Quantity", dataIndex: "quantity" }]} /></>}
    </Drawer>
    <Modal title="Create and Post" open={open} width={800} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={send.busy}>
      <Form name={`${section}-create`} form={form} layout="vertical" initialValues={{ date: today(), kind: config.create[0], lines: [{ quantity: 1 }] }} onFinish={v => {
        if (kind === "journal") send.submit("journals", { date: v.date, description: v.description, reference: v.reference || "", lines: [{ account_id: v.debit_id, debit: String(v.total) }, { account_id: v.credit_id, credit: String(v.total) }] });
        else send.submit("documents", { ...v, reference: v.reference || "", lines: v.lines.map((l: any) => ({ ...l, amount: String(l.amount), quantity: String(l.quantity || 1) })) });
      }}>
        <Form.Item name="kind" label="Type"><Select onChange={v => { form.resetFields(); form.setFieldsValue({ kind: v, date: today(), lines: [{ quantity: 1 }] }); setKind(v); }} options={config.create.map(value => ({ value, label: labels[value] }))} /></Form.Item>
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
    <Modal title={clearTitle} open={assignOpen} onCancel={() => setAssignOpen(false)} onOk={() => assignForm.submit()} confirmLoading={allocate.busy}>
      <Form name={`${section}-clear`} form={assignForm} layout="vertical" initialValues={{ date: today() }} onFinish={v => allocate.submit("assignments", { ...v, amount: String(v.amount) })}>
        <Alert type="info" message="Match an existing entry to an invoice or bill. Clearing does not post another accounting entry." style={{ marginBottom: 16 }} />
        <Form.Item name="payment_id" label="Available clearing entry" rules={[{ required: true }]}><Select onChange={() => assignForm.setFieldsValue({ document_id: undefined, amount: undefined })} options={data.rows.filter(t => t.settlement_side === side && Number(t.available) > 0).map(t => ({ value: t.id, label: `${t.number} · ${t.account} · Available ${money(t.available)}` }))} /></Form.Item>
        <Form.Item name="document_id" label="Invoice / bill" rules={[{ required: true }]}><Select options={data.rows.filter(t => t.kind === config.document && Number(t.remaining) > 0 && t.account_id === clearingSource?.account_id).map(t => ({ value: t.id, label: `${t.number} · ${t.account} · Outstanding ${money(t.remaining)}` }))} /></Form.Item>
        <DateField /><Form.Item name="amount" label="Clearing amount" rules={[{ required: true }]}><InputNumber min={0.01} precision={2} /></Form.Item>
      </Form></Modal>
  </Page>;
}

function OptionalClear({ form, accounts, transactions, direction, amount }: any) {
  const accountId = Form.useWatch("account_id", form), documentId = Form.useWatch("document_id", form);
  const account = accounts.find((a: any) => a.id === accountId);
  const kind = direction === "in" && account?.account_type === "RECEIVABLE" ? "invoice" : direction === "out" && account?.account_type === "PAYABLE" ? "bill" : null;
  if (!kind) return null;
  return <><Form.Item name="document_id" label="Clear a document now (optional)" preserve={false}>
    <Select allowClear placeholder="Leave blank to clear later" onChange={id => { const doc = transactions.find((t: any) => t.id === id); form.setFieldsValue({ clear_amount: doc ? Math.min(Number(amount || 0), Number(doc.remaining)) : undefined }); }} options={transactions.filter((t: any) => t.kind === kind && t.account_id === accountId && Number(t.remaining) > 0).map((t: any) => ({ value: t.id, label: `${t.number} · Outstanding ${money(t.remaining)}` }))} />
  </Form.Item>{documentId && <Form.Item name="clear_amount" label="Clearing amount" preserve={false} rules={[{ required: true }]}><InputNumber min={0.01} max={Number(amount)} precision={2} /></Form.Item>}</>;
}

export function Banking() {
  const data = useRows("transactions"), accounts = useRows("accounts"), user = useUser();
  const [open, setOpen] = useState(false), [target, setTarget] = useState<any>(), [bankId, setBankId] = useState<number>(), [status, setStatus] = useState("all");
  const [form] = Form.useForm(), [resolveForm] = Form.useForm();
  const direction = Form.useWatch("direction", form) || "in", amount = Form.useWatch("amount", form);
  const refresh = () => { accounts.refresh(); return data.refresh(); };
  const create = useSubmit(refresh, () => { setOpen(false); form.resetFields(); }), resolve = useSubmit(refresh, () => { setTarget(undefined); resolveForm.resetFields(); });
  const banks = accounts.rows.filter(a => a.account_type === "BANK");
  const allRows = data.rows.flatMap(t => (t.bank_entries || []).map((b: any) => ({ ...t, row_id: `${t.id}-${b.account_id}`, bank: b.account, bank_id: b.account_id, income: Number(b.amount) > 0 ? b.amount : "0", withdrawal: Number(b.amount) < 0 ? String(-Number(b.amount)) : "0", pending: t.kind === "suspense" && !t.resolved })));
  const rows = allRows.filter(t => (!bankId || t.bank_id === bankId) && (status === "all" || t.pending === (status === "pending")));
  const payload = (v: any) => ({ ...v, ...(v.amount !== undefined ? { amount: String(v.amount) } : {}), ...(v.document_id ? { clear_amount: String(v.clear_amount) } : { document_id: null, clear_amount: null }) });
  return <Page title="Banking" extra={user.role !== "Viewer" && <Button type="primary" onClick={() => { form.resetFields(); form.setFieldsValue({ date: today(), direction: "in" }); setOpen(true); }}>New Bank Transaction</Button>}>
    <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>{banks.map(a => <Col key={a.id} xs={24} md={8}><Card><Statistic title={a.name} value={Number(a.balance)} precision={2} prefix="$" /></Card></Col>)}</Row>
    <Card><Space wrap style={{ marginBottom: 16 }}>
      <Select allowClear placeholder="All bank / petty cash accounts" style={{ width: 260 }} value={bankId} onChange={setBankId} options={banks.map(a => ({ value: a.id, label: a.name }))} />
      <Select value={status} onChange={setStatus} style={{ width: 160 }} options={[{ value: "all", label: "All transactions" }, { value: "pending", label: "Unclassified" }, { value: "classified", label: "Classified" }]} />
      <Button onClick={() => exportTransactions(rows, "banking", ["date", "number", "bank", "description", "income", "withdrawal"])}>Export CSV</Button>
    </Space><Table rowKey="row_id" loading={data.loading || accounts.loading} dataSource={rows} columns={[
      { title: "Date", dataIndex: "date" }, { title: "Bank / petty cash", dataIndex: "bank" }, { title: "Number", dataIndex: "number" }, { title: "Description", dataIndex: "description" },
      { title: "Money in", dataIndex: "income", align: "right", render: money }, { title: "Money out", dataIndex: "withdrawal", align: "right", render: money },
      { title: "Status", render: (_, t) => <Tag color={t.pending ? "orange" : "green"}>{t.pending ? "Unclassified" : "Classified"}</Tag> },
      { title: "", render: (_, t) => t.pending && user.role !== "Viewer" && <Button onClick={() => { resolveForm.resetFields(); resolveForm.setFieldsValue({ date: today(), description: t.description }); setTarget(t); }}>Classify</Button> }
    ]} /></Card>
    <Modal title="New Bank Transaction" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={create.busy}>
      <Form name="bank-entry" form={form} layout="vertical" onFinish={v => create.submit("bank-transactions", { ...payload(v), account_id: v.account_id ?? null })}>
        <Form.Item name="bank_id" label="Bank / petty cash account" rules={[{ required: true }]}><AccountsSelect accounts={banks} /></Form.Item>
        <Form.Item name="direction" label="Direction" rules={[{ required: true }]}><Select onChange={() => form.setFieldsValue({ document_id: undefined, clear_amount: undefined })} options={[{ value: "in", label: "Money in" }, { value: "out", label: "Money out" }]} /></Form.Item>
        <DateField /><Form.Item name="description" label="Description" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="reference" label="Reference"><Input /></Form.Item>
        <Form.Item name="amount" label="Amount" rules={[{ required: true }]}><InputNumber precision={2} min={0.01} /></Form.Item>
        <Form.Item name="account_id" label="Classification account (optional)" extra="Leave blank if unknown; the entry will be posted to Suspense."><AccountsSelect allowClear accounts={accounts.rows.filter(a => a.name !== "Suspense" && a.account_type !== "BANK")} onChange={() => form.setFieldsValue({ document_id: undefined, clear_amount: undefined })} /></Form.Item>
        <OptionalClear form={form} accounts={accounts.rows} transactions={data.rows} direction={direction} amount={amount} />
      </Form>
    </Modal>
    <Modal title={`Classify Transaction · ${money(target?.amount)} USD`} open={!!target} onCancel={() => setTarget(undefined)} onOk={() => resolveForm.submit()} confirmLoading={resolve.busy}>
      <Form name="bank-classify" form={resolveForm} layout="vertical" onFinish={v => resolve.submit(`suspense/${target.id}/resolve`, payload(v))}>
        <Alert type="info" message="Reclassify the existing bank entry. You can clear a matching invoice or bill now, or leave it for later. The bank amount is not posted again." style={{ marginBottom: 16 }} />
        <Form.Item name="account_id" label="Classification account" rules={[{ required: true }]}><AccountsSelect accounts={accounts.rows.filter(a => a.name !== "Suspense" && a.account_type !== "BANK")} onChange={() => resolveForm.setFieldsValue({ document_id: undefined, clear_amount: undefined })} /></Form.Item>
        <DateField /><Form.Item name="description" label="Reason for classification" rules={[{ required: true }]}><Input /></Form.Item>
        <OptionalClear form={resolveForm} accounts={accounts.rows} transactions={data.rows} direction={target?.direction} amount={target?.amount} />
      </Form>
    </Modal>
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
      <Typography.Paragraph type="secondary">{report.basis === "as_at" ? `As at ${report.end}. Current year earnings include activity from ${report.start}; the selected start date does not filter closing balances.` : `Activity from ${report.start} to ${report.end}.`}</Typography.Paragraph>
      {report.reconciliation_difference !== null && <Alert style={{ marginBottom: 16 }} type={Number(report.reconciliation_difference) === 0 ? "success" : "error"} showIcon message={Number(report.reconciliation_difference) === 0 ? "Reconciled · Difference 0.00" : `Reconciliation difference: ${money(report.reconciliation_difference)} USD`} />}
      <Table<any> className="report-print" rowKey={r => `${r.section}-${r.account_code}-${r.account}`} pagination={false} dataSource={report.rows} locale={{ emptyText: "No activity in the selected period" }} columns={[
        { title: "Section", dataIndex: "section" }, { title: "Code", dataIndex: "account_code" }, { title: name === "cashflow" ? "Item" : "Account", dataIndex: "account" },
        { title: "Group", dataIndex: "category" }, ...(name === "trial" ? [
          { title: "Debit (USD)", dataIndex: "debit", align: "right" as const, render: money },
          { title: "Credit (USD)", dataIndex: "credit", align: "right" as const, render: money }
        ] : [{ title: "Amount (USD)", dataIndex: "amount", align: "right" as const, render: money }])]} />
      {name !== "trial" && <Descriptions style={{ marginTop: 20 }} column={3} items={Object.entries(report.section_totals).map(([key, value]) => ({ key, label: key, children: money(value) }))} />}
      <Typography.Text type="secondary">{name === "balance" ? "Current year earnings are calculated automatically for this report; no closing journal is required to display them. Credit balances in liabilities and equity are shown as positive." : name === "income" ? "Revenue uses credit-positive amounts; expenses use debit-positive amounts. Net profit equals total revenue minus total expenses." : name === "cashflow" ? "Indirect cash flow: profit plus balance-sheet adjustments. Beginning cash plus net cash flow must equal the cashbook balance." : "Closing balances are shown separately as debits and credits, before year-end closing."}</Typography.Text>
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
