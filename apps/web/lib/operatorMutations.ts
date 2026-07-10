import 'server-only';

import { redirect } from 'next/navigation';

const ENABLED_VALUE = 'true';
export const READ_ONLY_OPERATOR_DESTINATION = '/?read_only=1';

export function operatorMutationsEnabled(): boolean {
  return process.env.OPERATOR_UI_ENABLED?.trim().toLowerCase() === ENABLED_VALUE;
}

export function requireOperatorMutationsEnabled(): void {
  if (!operatorMutationsEnabled()) {
    redirect(READ_ONLY_OPERATOR_DESTINATION);
  }
}
