// Adapted from Refine official examples/auth-antd. See NOTICE.md.
import { Authenticated, Refine } from "@refinedev/core";
import { AuthPage, ErrorComponent, ThemedLayout, RefineThemes, useNotificationProvider } from "@refinedev/antd";
import routerProvider, { CatchAllNavigate, NavigateToResource, DocumentTitleHandler } from "@refinedev/react-router";
import { BrowserRouter, Route, Routes, Outlet, Navigate } from "react-router";
import { App as AntdApp, ConfigProvider } from "antd";
import enUS from "antd/locale/en_US";
import { DashboardOutlined, BankOutlined, SwapOutlined, FileTextOutlined, ShoppingCartOutlined, ShopOutlined, TeamOutlined } from "@ant-design/icons";
import "@ant-design/v5-patch-for-react-19";
import "@refinedev/antd/dist/reset.css";
import { authProvider, dataProvider, api } from "./api";
import { Dashboard, Accounts, Transactions, Banking, Reports, Users, Password } from "./pages";
import "./style.css";

export default function App() {
  return <BrowserRouter><ConfigProvider locale={enUS} theme={{ ...RefineThemes.Blue,
    token: { ...RefineThemes.Blue.token, colorPrimary: "#2563eb", borderRadius: 8 } }}><AntdApp>
    <Refine authProvider={authProvider} dataProvider={dataProvider} routerProvider={routerProvider}
      notificationProvider={useNotificationProvider}
      accessControlProvider={{ can: async ({ resource, action }) => {
        const role = (await api("users/me")).role;
        return { can: resource?.startsWith("admin/") ? role === "Admin" : action === "list" || action === "show" || role !== "Viewer" };
      } }}
      resources={[
        { name: "dashboard", list: "/", meta: { label: "Overview", icon: <DashboardOutlined /> } },
        { name: "accounts", list: "/accounts", meta: { label: "Chart of Accounts", icon: <BankOutlined /> } },
        { name: "journal", list: "/journal", meta: { label: "Journal", icon: <SwapOutlined /> } },
        { name: "sales", list: "/sales", meta: { label: "Sales", icon: <ShopOutlined /> } },
        { name: "purchases", list: "/purchases", meta: { label: "Purchases", icon: <ShoppingCartOutlined /> } },
        { name: "banking", list: "/banking", meta: { label: "Banking", icon: <BankOutlined /> } },
        { name: "reports", list: "/reports", meta: { label: "Financial Reports", icon: <FileTextOutlined /> } },
        { name: "admin/users", list: "/users", meta: { label: "Users & Roles", icon: <TeamOutlined /> } },
        { name: "password", list: "/password", meta: { label: "Change Password" } },
      ]} options={{ syncWithLocation: true }}>
      <Routes>
        <Route element={<Authenticated key="protected" fallback={<CatchAllNavigate to="/login" />}>
          <ThemedLayout Title={() => <strong style={{ fontSize: 20, color: "#1e40af" }}>Simple Books</strong>}><Outlet /></ThemedLayout>
        </Authenticated>}>
          <Route index element={<Dashboard />} /><Route path="accounts" element={<Accounts />} />
          <Route path="journal" element={<Transactions key="journal" section="journal" />} />
          <Route path="sales" element={<Transactions key="sales" section="sales" />} />
          <Route path="purchases" element={<Transactions key="purchases" section="purchases" />} />
          <Route path="banking" element={<Banking />} />
          <Route path="transactions" element={<Navigate to="/journal" replace />} /><Route path="pending" element={<Navigate to="/banking?tab=pending" replace />} />
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
