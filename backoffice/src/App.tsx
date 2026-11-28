import { Route } from "react-router-dom";
import { Admin, CustomRoutes, Resource } from "react-admin";
import type { UserManager } from "oidc-client-ts";
import type { Api } from "./api";
import { authProvider } from "./auth";
import { config } from "./config";
import { BackOfficeContext } from "./context";
import { dataProvider } from "./dataProvider";
import { Home } from "./Home";
import { ICONS, icon } from "./icons";
import { Login } from "./Login";
import { screensFor } from "./screens";
import { Layout } from "./Layout";
import { darkTheme, lightTheme } from "./theme";
import { lazyPage } from "./lazyPage";

// Every screen but the first (My work) loads when it is first opened, so the
// first screen downloads only what it shows (kept light, 12 Nov 2026).
const PolicyPages = lazyPage(() => import("./Policies"), "PolicyPages");
const TradersPage = lazyPage(() => import("./Traders"), "TradersPage");
const OfferingPage = lazyPage(() => import("./Offering"), "OfferingPage");
const ReportsPage = lazyPage(() => import("./Transactions"), "ReportsPage");
const SystemPage = lazyPage(() => import("./System"), "SystemPage");
const SiteEditorPage = lazyPage(() => import("./Site"), "SiteEditorPage");
const NoticesPage = lazyPage(() => import("./Notices"), "NoticesPage");
const PaymentMethodsPage = lazyPage(() => import("./PaymentMethods"), "PaymentMethodsPage");
const SettingsHub = lazyPage(() => import("./SettingsHub"), "SettingsHub");
const GeneralPage = lazyPage(() => import("./SettingsHub"), "GeneralPage");
const SecurityPage = lazyPage(() => import("./SettingsHub"), "SecurityPage");
const AnomalyReport = lazyPage(() => import("./Reports"), "AnomalyReport");
const AuditTrail = lazyPage(() => import("./Reports"), "AuditTrail");
const NewsDesk = lazyPage(() => import("./Reports"), "NewsDesk");
const CorrectionsPage = lazyPage(() => import("./Corrections"), "CorrectionsPage");
const ClustersPage = lazyPage(() => import("./Clusters"), "ClustersPage");
const ClassesPage = lazyPage(() => import("./Classes"), "ClassesPage");
const ScalingPage = lazyPage(() => import("./Scaling"), "ScalingPage");
const CouponsPage = lazyPage(() => import("./Coupons"), "CouponsPage");
const ReferralsPage = lazyPage(() => import("./Referrals"), "ReferralsPage");
const BooksPage = lazyPage(() => import("./Books"), "BooksPage");
const ReconciliationPage = lazyPage(() => import("./Reconciliation"), "ReconciliationPage");
const DisputesPage = lazyPage(() => import("./Disputes"), "DisputesPage");
const ReceiptsPage = lazyPage(() => import("./Receipts"), "ReceiptsPage");
const TransfersPage = lazyPage(() => import("./Transfers"), "TransfersPage");
const LiabilitiesPage = lazyPage(() => import("./Liabilities"), "LiabilitiesPage");
const ExposurePage = lazyPage(() => import("./Exposure"), "ExposurePage");
const StaffProfilePage = lazyPage(() => import("./StaffProfile"), "StaffProfilePage");
const CaseList = lazyPage(() => import("./resources/cases"), "CaseList");
const CaseShow = lazyPage(() => import("./resources/cases"), "CaseShow");
const NotificationList = lazyPage(() => import("./resources/notifications"), "NotificationList");
const PayoutList = lazyPage(() => import("./resources/payouts"), "PayoutList");
const PayoutShow = lazyPage(() => import("./resources/payouts"), "PayoutShow");
const OnboardingList = lazyPage(() => import("./resources/reviews"), "OnboardingList");
const OnboardingShow = lazyPage(() => import("./resources/reviews"), "OnboardingShow");
const ProgressionList = lazyPage(() => import("./resources/reviews"), "ProgressionList");
const ProgressionShow = lazyPage(() => import("./resources/reviews"), "ProgressionShow");
const ChangeList = lazyPage(() => import("./resources/settings"), "ChangeList");
const ProposalList = lazyPage(() => import("./resources/settings"), "ProposalList");
const ApprenticeProposalList = lazyPage(() => import("./resources/staff"), "ApprenticeProposalList");
const CutoffList = lazyPage(() => import("./resources/staff"), "CutoffList");
const GrantList = lazyPage(() => import("./resources/staff"), "GrantList");
const MemberList = lazyPage(() => import("./resources/staff"), "MemberList");
const MemberShow = lazyPage(() => import("./resources/staff"), "MemberShow");

export function App({ api, manager }: { api: Api; manager: UserManager }) {
  return (
    <BackOfficeContext.Provider value={{ api, manager }}>
      <Admin
        title={`${config.brand} back office`}
        authProvider={authProvider(manager, api)}
        dataProvider={dataProvider(api)}
        dashboard={Home}
        loginPage={Login}
        layout={Layout}
        theme={lightTheme}
        darkTheme={darkTheme}
        requireAuth
        disableTelemetry
      >
        {(permissions: string[] | undefined) => {
          const screens = screensFor(permissions ?? []);
          return (
            <>
              {screens.has("payouts") ? <Resource name="payouts" icon={icon(ICONS.payouts)} list={PayoutList} show={PayoutShow} /> : null}
              {screens.has("cases") ? <Resource name="cases" icon={icon(ICONS.cases)} list={CaseList} show={CaseShow} /> : null}
              {screens.has("onboardings") ? (
                <Resource name="onboardings" icon={icon(ICONS.onboardings)} options={{ label: "Identity reviews" }} list={OnboardingList} show={OnboardingShow} />
              ) : null}
              {screens.has("progressions") ? (
                <Resource name="progressions" icon={icon(ICONS.progressions)} options={{ label: "Funded reviews" }} list={ProgressionList} show={ProgressionShow} />
              ) : null}
              <Resource name="apprentice-proposals" icon={icon(ICONS.apprenticeProposals)} options={{ label: "Apprentice proposals" }} list={ApprenticeProposalList} />
              <Resource name="notifications" icon={icon(ICONS.notifications)} list={NotificationList} />
              {screens.has("members") ? <Resource name="members" icon={icon(ICONS.members)} options={{ label: "Staff" }} list={MemberList} show={MemberShow} /> : null}
              {screens.has("grants") ? <Resource name="grants" icon={icon(ICONS.grants)} options={{ label: "Access requests" }} list={GrantList} /> : null}
              {screens.has("cutoffs") ? <Resource name="cutoffs" icon={icon(ICONS.cutoffs)} options={{ label: "Leaver cut-offs" }} list={CutoffList} /> : null}
              {screens.has("proposals") ? <Resource name="proposals" icon={icon(ICONS.proposals)} options={{ label: "Setting proposals" }} list={ProposalList} /> : null}
              {screens.has("changes") ? <Resource name="changes" icon={icon(ICONS.changes)} options={{ label: "Setting changes" }} list={ChangeList} /> : null}
              <CustomRoutes>
                {screens.has("traders") ? <Route path="/traders" element={<TradersPage />} /> : null}
                {screens.has("offering") ? <Route path="/offering" element={<OfferingPage />} /> : null}
                {screens.has("transactions") ? <Route path="/transactions" element={<ReportsPage />} /> : null}
                {screens.has("system") ? <Route path="/system" element={<SystemPage />} /> : null}
                {screens.has("anomalies") ? <Route path="/anomalies" element={<AnomalyReport />} /> : null}
                {screens.has("audit") ? <Route path="/audit" element={<AuditTrail />} /> : null}
                {screens.has("news") ? <Route path="/news" element={<NewsDesk />} /> : null}
                {screens.has("cases") ? <Route path="/corrections" element={<CorrectionsPage />} /> : null}
                {screens.has("cases") ? <Route path="/clusters" element={<ClustersPage />} /> : null}
                {screens.has("cases") ? <Route path="/classes" element={<ClassesPage />} /> : null}
                {screens.has("progressions") ? <Route path="/scaling" element={<ScalingPage />} /> : null}
                {screens.has("settings") ? <Route path="/coupons" element={<CouponsPage />} /> : null}
                {screens.has("settings") ? <Route path="/referrals" element={<ReferralsPage />} /> : null}
                {screens.has("settings") ? <Route path="/books" element={<BooksPage />} /> : null}
                {screens.has("settings") ? <Route path="/reconciliation" element={<ReconciliationPage />} /> : null}
                {screens.has("settings") ? <Route path="/disputes" element={<DisputesPage />} /> : null}
                {screens.has("settings") ? <Route path="/receipts" element={<ReceiptsPage />} /> : null}
                {screens.has("settings") ? <Route path="/transfers" element={<TransfersPage />} /> : null}
                {screens.has("settings") ? <Route path="/liabilities" element={<LiabilitiesPage />} /> : null}
                {screens.has("settings") ? <Route path="/exposure" element={<ExposurePage />} /> : null}
                <Route path="/profile" element={<StaffProfilePage />} />
                {screens.has("policies") ? <Route path="/policies" element={<PolicyPages />} /> : null}
                {screens.has("site") ? <Route path="/site" element={<SiteEditorPage />} /> : null}
                {screens.has("settings") ? <Route path="/settings" element={<SettingsHub />} /> : null}
                {screens.has("settings") ? <Route path="/security" element={<SecurityPage />} /> : null}
                {screens.has("settings") ? <Route path="/general" element={<GeneralPage />} /> : null}
                {screens.has("templates") ? <Route path="/notices" element={<NoticesPage />} /> : null}
                {screens.has("methods") ? <Route path="/payment-methods" element={<PaymentMethodsPage />} /> : null}
              </CustomRoutes>
            </>
          );
        }}
      </Admin>
    </BackOfficeContext.Provider>
  );
}
