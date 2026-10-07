import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

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
import CollectionsWork from './pages/CollectionsWork.jsx'
import Currencies from './pages/Currencies.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Funding from './pages/Funding.jsx'
import Groups from './pages/Groups.jsx'
import GoLive from './pages/GoLive.jsx'
import Jobs from './pages/Jobs.jsx'
import Journals from './pages/Journals.jsx'
import Ledger from './pages/Ledger.jsx'
import LoanBookImport from './pages/LoanBookImport.jsx'
import LoanDetail from './pages/LoanDetail.jsx'
import LoanNew from './pages/LoanNew.jsx'
import Loans from './pages/Loans.jsx'
import Login from './pages/Login.jsx'
import Notifications from './pages/Notifications.jsx'
import BulkMessages from './pages/BulkMessages.jsx'
import OnlineApplications from './pages/OnlineApplications.jsx'
import Payouts from './pages/Payouts.jsx'
import Screening from './pages/Screening.jsx'
import {
  CommunicationsAutomation,
  CommunicationsChannels,
  CommunicationsOverview,
  CommunicationsWording,
} from './pages/Communications.jsx'
import Payments from './pages/Payments.jsx'
import PortalRequests from './pages/PortalRequests.jsx'
import Payroll from './pages/Payroll.jsx'
import Performance from './pages/Performance.jsx'
import Spreadsheets from './pages/Spreadsheets.jsx'
import Periods from './pages/Periods.jsx'
import Products from './pages/Products.jsx'
import Provisioning from './pages/Provisioning.jsx'
import Savings from './pages/Savings.jsx'
import Settings from './pages/Settings.jsx'
import Till from './pages/Till.jsx'
import Transactions from './pages/Transactions.jsx'
import Users from './pages/Users.jsx'
import Apply from './portal/Apply.jsx'
import Portal from './portal/Portal.jsx'

/** Blocks a route for users without any of the named access rights. */
function RequireRight({ rights, children }) {
  const { can } = useAuth()
  if (!can(...rights)) return <Navigate to="/dashboard" replace />
  return children
}

export default function App() {
  const { status } = useAuth()
  const { pathname } = useLocation()

  // The borrower portal stands apart from the staff application: its own sign-in,
  // its own token, and no staff page reachable from it.
  if (pathname === '/apply') {
    return (
      <Routes>
        <Route path="/apply" element={<Apply />} />
      </Routes>
    )
  }
  if (pathname === '/portal' || pathname.startsWith('/portal/')) {
    return (
      <Routes>
        <Route path="/portal/*" element={<Portal />} />
      </Routes>
    )
  }

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
          <Route path="/portal-requests" element={<PortalRequests />} />
          <Route path="/online-applications" element={<OnlineApplications />} />
          <Route path="/screening" element={<Screening />} />
          <Route path="/payouts" element={<Payouts />} />
          <Route path="/savings" element={<Savings />} />

          <Route path="/loans" element={<Loans />} />
          <Route path="/loans/new" element={<LoanNew />} />
          <Route path="/loans/:id" element={<LoanDetail />} />

          <Route path="/collections" element={<Collections />} />
          <Route path="/collections/work" element={<CollectionsWork />} />
          <Route
            path="/till"
            element={
              <RequireRight rights={['cash']}>
                <Till />
              </RequireRight>
            }
          />
          <Route path="/arrears" element={<Arrears />} />
          <Route path="/payroll" element={<Payroll />} />
          <Route
            path="/imports"
            element={
              <RequireRight rights={['cash']}>
                <BulkImport />
              </RequireRight>
            }
          />
          <Route
            path="/imports/loan-book"
            element={
              <RequireRight rights={['admin']}>
                <LoanBookImport />
              </RequireRight>
            }
          />
          <Route path="/notifications" element={<Notifications />} />
          <Route path="/communications" element={<CommunicationsOverview />} />
          <Route path="/communications/bulk" element={<BulkMessages />} />
          <Route path="/communications/automation" element={<CommunicationsAutomation />} />
          <Route path="/communications/wording" element={<CommunicationsWording />} />
          <Route path="/communications/channels" element={<CommunicationsChannels />} />
          <Route path="/payments" element={<Payments />} />

          <Route path="/transactions" element={<Transactions />} />
          <Route path="/ledger" element={<Ledger />} />
          <Route path="/journals" element={<Journals />} />
          <Route path="/bank-reconciliation" element={<BankRec />} />
          <Route path="/funding" element={<Funding />} />
          <Route path="/currencies" element={<Currencies />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/spreadsheets" element={<Spreadsheets />} />
          <Route path="/provisioning" element={<Provisioning />} />
          <Route path="/periods" element={<Periods />} />


          <Route path="/products" element={<Products />} />
          <Route path="/charges" element={<Charges />} />
          <Route path="/account" element={<Account />} />
          <Route
            path="/users"
            element={
              <RequireRight rights={['admin']}>
                <Users />
              </RequireRight>
            }
          />
          <Route
            path="/settings"
            element={
              <RequireRight rights={['admin']}>
                <Settings />
              </RequireRight>
            }
          />
          <Route
            path="/go-live"
            element={
              <RequireRight rights={['admin']}>
                <GoLive />
              </RequireRight>
            }
          />
          <Route
            path="/jobs"
            element={
              <RequireRight rights={['admin']}>
                <Jobs />
              </RequireRight>
            }
          />
          <Route
            path="/audit"
            element={
              <RequireRight rights={['admin']}>
                <Audit />
              </RequireRight>
            }
          />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </OrgProvider>
  )
}
