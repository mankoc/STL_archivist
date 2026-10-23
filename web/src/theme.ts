// The client's Mantine theme: rounded corners throughout — boxes (cards,
// modals, menus) more than fields, and pill-shaped buttons.
import {
  ActionIcon,
  Alert,
  Button,
  Card,
  createTheme,
  Menu,
  Modal,
  Notification,
  Paper,
  Popover,
  SegmentedControl,
  Tooltip,
} from "@mantine/core";

export const theme = createTheme({
  // Inputs, checkboxes, table highlights, images and anything not listed below.
  defaultRadius: "md",
  components: {
    Button: Button.extend({ defaultProps: { radius: "xl" } }),
    ActionIcon: ActionIcon.extend({ defaultProps: { radius: "xl" } }),
    SegmentedControl: SegmentedControl.extend({ defaultProps: { radius: "xl" } }),
    Card: Card.extend({ defaultProps: { radius: "lg" } }),
    Paper: Paper.extend({ defaultProps: { radius: "lg" } }),
    Alert: Alert.extend({ defaultProps: { radius: "lg" } }),
    Modal: Modal.extend({ defaultProps: { radius: "lg" } }),
    Menu: Menu.extend({ defaultProps: { radius: "md" } }),
    Popover: Popover.extend({ defaultProps: { radius: "md" } }),
    Tooltip: Tooltip.extend({ defaultProps: { radius: "md" } }),
    Notification: Notification.extend({ defaultProps: { radius: "lg" } }),
  },
});
