import { Navigate, Route, Routes } from 'react-router-dom'

import Layout from './components/Layout.jsx'
import { Loading } from './components/ui.jsx'
import { useAuth } from './lib/auth.jsx'
import { OrgProvider } from './lib/org.jsx'
import Account from './pages/Account.jsx'
import Arrears from './pages/Arrears.jsx'
import Audit from './pages/Audit.jsx'
import BankRec from './pages/BankRec.jsx'
import BorrowerDetail from './pages/BorrowerDetail.jsx'
import BorrowerForm from './pages/BorrowerForm.jsx'
import Borrowers from './pages/Borrowers.jsx'
import BulkImport from './pages/BulkImport.jsx'
import Charges from './pages/Charges.jsx'
import Collections from './pages/Collections.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Funding from './pages/Funding.jsx'
import Groups from './pages/Groups.jsx'
import Journals from './pages/Journals.jsx'
import Ledger from './pages/Ledger.jsx'
import LoanBookImport from './pages/LoanBookImport.jsx'
import LoanDetail from './pages/LoanDetail.jsx'
import LoanNew from './pages/LoanNew.jsx'
import Loans from './pages/Loans.jsx'
import Login from './pages/Login.jsx'
import Notifications from './pages/Notifications.jsx'
import Payroll from './pages/Payroll.jsx'
import Performance from './pages/Performance.jsx'
import Periods from './pages/Periods.jsx'
import Products from './pages/Products.jsx'
import Provisioning from './pages/Provisioning.jsx'
import Risks from './pages/Risks.jsx'
import Savings from './pages/Savings.jsx'
import Settings from './pages/Settings.jsx'
import Till from './pages/Till.jsx'
import Transactions from './pages/Transactions.jsx'
import Users from './pages/Users.jsx'

/** Blocks a route for roles that must not see it at all. */
function RequireRole({ roles, children }) {
  const { can } = useAuth()
  if (!can(...roles)) return <Navigate to="/dashboard" replace />
  return children
}

export default function App() {
  const { status } = useAuth()

  if (status === 'loading') return <Loading what="Restoring your session" />
  if (status !== 'signed-in') return <Login />

  return (
    <OrgProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />

          <Route path="/borrowers" element={<Borrowers />} />
          <Route path="/borrowers/new" element={<BorrowerForm />} />
          <Route path="/borrowers/:id" element={<BorrowerDetail />} />
          <Route path="/borrowers/:id/edit" element={<BorrowerForm />} />
          <Route path="/groups" element={<Groups />} />
          <Route path="/savings" element={<Savings />} />

          <Route path="/loans" element={<Loans />} />
          <Route path="/loans/new" element={<LoanNew />} />
          <Route path="/loans/:id" element={<LoanDetail />} />

          <Route path="/collections" element={<Collections />} />
          <Route
            path="/till"
            element={
              <RequireRole roles={['admin', 'loan_officer', 'teller']}>
                <Till />
              </RequireRole>
            }
          />
          <Route path="/arrears" element={<Arrears />} />
          <Route path="/payroll" element={<Payroll />} />
          <Route
            path="/imports"
            element={
              <RequireRole roles={['admin', 'loan_officer', 'teller']}>
                <BulkImport />
              </RequireRole>
            }
          />
          <Route
            path="/imports/loan-book"
            element={
              <RequireRole roles={['admin']}>
                <LoanBookImport />
              </RequireRole>
            }
          />
          <Route path="/notifications" element={<Notifications />} />

          <Route path="/transactions" element={<Transactions />} />
          <Route path="/ledger" element={<Ledger />} />
          <Route path="/journals" element={<Journals />} />
          <Route path="/bank-reconciliation" element={<BankRec />} />
          <Route path="/funding" element={<Funding />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/provisioning" element={<Provisioning />} />
          <Route path="/periods" element={<Periods />} />

          <Route path="/risks" element={<Risks />} />

          <Route path="/products" element={<Products />} />
          <Route path="/charges" element={<Charges />} />
          <Route path="/account" element={<Account />} />
          <Route
            path="/users"
            element={
              <RequireRole roles={['admin']}>
                <Users />
              </RequireRole>
            }
          />
          <Route
            path="/settings"
            element={
              <RequireRole roles={['admin']}>
                <Settings />
              </RequireRole>
            }
          />
          <Route
            path="/audit"
            element={
              <RequireRole roles={['admin']}>
                <Audit />
              </RequireRole>
            }
          />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </OrgProvider>
  )
}
