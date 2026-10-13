// Adapted from Refine official examples/auth-antd. See NOTICE.md.
import { Authenticated, Refine } from "@refinedev/core";
import { AuthPage, ErrorComponent, ThemedLayout, RefineThemes, useNotificationProvider } from "@refinedev/antd";
import routerProvider, { CatchAllNavigate, NavigateToResource, DocumentTitleHandler } from "@refinedev/react-router";
import { BrowserRouter, Route, Routes, Outlet } from "react-router";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { DashboardOutlined, BankOutlined, SwapOutlined, FileTextOutlined, QuestionCircleOutlined, TeamOutlined } from "@ant-design/icons";
import "@ant-design/v5-patch-for-react-19";
import "@refinedev/antd/dist/reset.css";
import { authProvider, dataProvider, api } from "./api";
import { Dashboard, Accounts, Transactions, Pending, Reports, Users, Password } from "./pages";
import "./style.css";

export default function App() {
  return <BrowserRouter><ConfigProvider locale={zhCN} theme={{ ...RefineThemes.Blue,
    token: { ...RefineThemes.Blue.token, colorPrimary: "#2563eb", borderRadius: 8 } }}><AntdApp>
    <Refine authProvider={authProvider} dataProvider={dataProvider} routerProvider={routerProvider}
      notificationProvider={useNotificationProvider}
      accessControlProvider={{ can: async ({ resource, action }) => {
        const role = (await api("users/me")).role;
        return { can: resource?.startsWith("admin/") ? role === "Admin" : action === "list" || action === "show" || role !== "Viewer" };
      } }}
      resources={[
        { name: "dashboard", list: "/", meta: { label: "概览", icon: <DashboardOutlined /> } },
        { name: "accounts", list: "/accounts", meta: { label: "科目与账户", icon: <BankOutlined /> } },
        { name: "transactions", list: "/transactions", meta: { label: "交易与发票", icon: <SwapOutlined /> } },
        { name: "pending", list: "/pending", meta: { label: "未知入账", icon: <QuestionCircleOutlined /> } },
        { name: "reports", list: "/reports", meta: { label: "财务报表", icon: <FileTextOutlined /> } },
        { name: "admin/users", list: "/users", meta: { label: "用户与角色", icon: <TeamOutlined /> } },
        { name: "password", list: "/password", meta: { label: "修改密码" } },
      ]} options={{ syncWithLocation: true }}>
      <Routes>
        <Route element={<Authenticated key="protected" fallback={<CatchAllNavigate to="/login" />}>
          <ThemedLayout Title={() => <strong style={{ fontSize: 20, color: "#1e40af" }}>Simple Books</strong>}><Outlet /></ThemedLayout>
        </Authenticated>}>
          <Route index element={<Dashboard />} /><Route path="accounts" element={<Accounts />} />
          <Route path="transactions" element={<Transactions />} /><Route path="pending" element={<Pending />} />
          <Route path="reports" element={<Reports />} /><Route path="users" element={<Users />} />
          <Route path="password" element={<Password />} />
        </Route>
        <Route element={<Authenticated key="login" fallback={<Outlet />}><NavigateToResource resource="dashboard" /></Authenticated>}>
          <Route path="login" element={<AuthPage type="login" title={<h2>Simple Books</h2>} registerLink={false} forgotPasswordLink={false} rememberMe={false} />} />
        </Route>
        <Route path="*" element={<ErrorComponent />} />
      </Routes><DocumentTitleHandler />
    </Refine></AntdApp></ConfigProvider></BrowserRouter>;
}
