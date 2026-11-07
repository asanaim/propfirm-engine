import { Menu as RaMenu, usePermissions } from "react-admin";
import { ICONS, SIZE, STROKE } from "./icons";
import { screensFor } from "./screens";

/** The resources, plus the report pages this login may read. */
export function Menu() {
  const { permissions } = usePermissions<string[]>();
  const screens = screensFor(permissions ?? []);
  return (
    <RaMenu>
      <RaMenu.DashboardItem primaryText="My work" leftIcon={<ICONS.work size={SIZE} strokeWidth={STROKE} />} />
      <RaMenu.ResourceItems />
      {screens.has("news") ? <RaMenu.Item to="/news" primaryText="News desk" leftIcon={<ICONS.news size={SIZE} strokeWidth={STROKE} />} /> : null}
      {screens.has("anomalies") ? (
        <RaMenu.Item to="/anomalies" primaryText="Anomaly report" leftIcon={<ICONS.anomalies size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
      {screens.has("audit") ? <RaMenu.Item to="/audit" primaryText="Security log" leftIcon={<ICONS.audit size={SIZE} strokeWidth={STROKE} />} /> : null}
    </RaMenu>
  );
}
