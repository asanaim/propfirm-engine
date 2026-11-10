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
import { PolicyPages } from "./Policies";
import { SiteEditorPage } from "./Site";
import { NoticesPage } from "./Notices";
import { PaymentMethodsPage } from "./PaymentMethods";
import { GeneralPage, SecurityPage, SettingsHub } from "./SettingsHub";
import { AnomalyReport, AuditTrail, NewsDesk } from "./Reports";
import { CaseList, CaseShow } from "./resources/cases";
import { NotificationList } from "./resources/notifications";
import { PayoutList, PayoutShow } from "./resources/payouts";
import { OnboardingList, OnboardingShow, ProgressionList, ProgressionShow } from "./resources/reviews";
import { ChangeList, ProposalList } from "./resources/settings";
import { screensFor } from "./screens";
import { Layout } from "./Layout";
import { darkTheme, lightTheme } from "./theme";
import { ApprenticeProposalList, CutoffList, GrantList, MemberList, MemberShow } from "./resources/staff";

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
                {screens.has("anomalies") ? <Route path="/anomalies" element={<AnomalyReport />} /> : null}
                {screens.has("audit") ? <Route path="/audit" element={<AuditTrail />} /> : null}
                {screens.has("news") ? <Route path="/news" element={<NewsDesk />} /> : null}
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
