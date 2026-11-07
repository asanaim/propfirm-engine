import AssessmentIcon from "@mui/icons-material/Assessment";
import EventIcon from "@mui/icons-material/Event";
import GppGoodIcon from "@mui/icons-material/GppGood";
import { Menu as RaMenu, usePermissions } from "react-admin";
import { screensFor } from "./screens";

/** The resources, plus the report pages this login may read. */
export function Menu() {
  const { permissions } = usePermissions<string[]>();
  const screens = screensFor(permissions ?? []);
  return (
    <RaMenu>
      <RaMenu.DashboardItem primaryText="My work" />
      <RaMenu.ResourceItems />
      {screens.has("news") ? <RaMenu.Item to="/news" primaryText="News desk" leftIcon={<EventIcon />} /> : null}
      {screens.has("anomalies") ? (
        <RaMenu.Item to="/anomalies" primaryText="Anomaly report" leftIcon={<AssessmentIcon />} />
      ) : null}
      {screens.has("audit") ? <RaMenu.Item to="/audit" primaryText="Security log" leftIcon={<GppGoodIcon />} /> : null}
    </RaMenu>
  );
}
