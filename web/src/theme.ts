import type { ThemeConfig } from 'antd'

// 全站视觉：浅灰底 + 白卡片、大圆角、克制阴影、靛蓝主色。
export const appTheme: ThemeConfig = {
  token: {
    colorPrimary: '#4f46e5',
    colorInfo: '#4f46e5',
    colorBgLayout: '#f5f6f8',
    borderRadius: 10,
    fontFamily:
      "'Inter', 'PingFang SC', 'HarmonyOS Sans SC', 'Microsoft YaHei', system-ui, -apple-system, sans-serif",
    fontSize: 14,
  },
  components: {
    Layout: { siderBg: '#ffffff', headerBg: '#ffffff', headerHeight: 56 },
    Card: { borderRadiusLG: 14, paddingLG: 20 },
    Menu: { itemBorderRadius: 10, itemMarginInline: 8 },
    Button: { fontWeight: 500 },
    Steps: { titleLineHeight: 28 },
  },
}
