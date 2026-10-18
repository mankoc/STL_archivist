// The signed-in user, for role-dependent UI.
import { createContext, useContext } from "react";

import type { Schemas } from "../api/client";

export type User = Schemas["User"];

export const UserContext = createContext<User | null>(null);

export function useUser(): User {
  const user = useContext(UserContext);
  if (!user) throw new Error("useUser outside a signed-in shell");
  return user;
}

const RANK = { viewer: 0, editor: 1, admin: 2 } as const;

/** Whether `user` has at least `role` (the server checks again). */
export function hasRole(user: User, role: User["role"]): boolean {
  return RANK[user.role] >= RANK[role];
}
