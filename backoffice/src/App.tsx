import CandlestickChartIcon from "@mui/icons-material/CandlestickChart";
import PeopleIcon from "@mui/icons-material/People";
import { Route } from "react-router-dom";
import { AccountList, CustomerList, CustomerShow } from "./resources/customers";
import { Admin, CustomRoutes, Resource } from "react-admin";
import type { UserManager } from "oidc-client-ts";
import type { Api } from "./api";
import { authProvider } from "./auth";
import { config } from "./config";
import { BackOfficeContext } from "./context";
import { dataProvider } from "./dataProvider";
import { Home } from "./Home";
import { Login } from "./Login";
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
              {screens.has("customers") ? <Resource name="customers" icon={PeopleIcon} list={CustomerList} show={CustomerShow} /> : null}
              {screens.has("accounts") ? <Resource name="accounts" icon={CandlestickChartIcon} options={{ label: "Trading accounts" }} list={AccountList} /> : null}
              {screens.has("payouts") ? <Resource name="payouts" list={PayoutList} show={PayoutShow} /> : null}
              {screens.has("cases") ? <Resource name="cases" list={CaseList} show={CaseShow} /> : null}
              {screens.has("onboardings") ? (
                <Resource name="onboardings" options={{ label: "Identity reviews" }} list={OnboardingList} show={OnboardingShow} />
              ) : null}
              {screens.has("progressions") ? (
                <Resource name="progressions" options={{ label: "Funded reviews" }} list={ProgressionList} show={ProgressionShow} />
              ) : null}
              <Resource name="apprentice-proposals" options={{ label: "Apprentice proposals" }} list={ApprenticeProposalList} />
              <Resource name="notifications" list={NotificationList} />
              {screens.has("members") ? <Resource name="members" options={{ label: "Staff" }} list={MemberList} show={MemberShow} /> : null}
              {screens.has("grants") ? <Resource name="grants" options={{ label: "Access requests" }} list={GrantList} /> : null}
              {screens.has("cutoffs") ? <Resource name="cutoffs" options={{ label: "Leaver cut-offs" }} list={CutoffList} /> : null}
              {screens.has("proposals") ? <Resource name="proposals" options={{ label: "Setting proposals" }} list={ProposalList} /> : null}
              {screens.has("changes") ? <Resource name="changes" options={{ label: "Setting changes" }} list={ChangeList} /> : null}
              <CustomRoutes>
                {screens.has("anomalies") ? <Route path="/anomalies" element={<AnomalyReport />} /> : null}
                {screens.has("audit") ? <Route path="/audit" element={<AuditTrail />} /> : null}
                {screens.has("news") ? <Route path="/news" element={<NewsDesk />} /> : null}
              </CustomRoutes>
            </>
          );
        }}
      </Admin>
    </BackOfficeContext.Provider>
  );
}
