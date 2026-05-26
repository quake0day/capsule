// Thin TypeScript mirror of capsule.yaml (spec v0.1).
//
// This is NOT a validator — capsule validate (the Python CLI) is the
// authoritative source of truth. These types exist so the server can render
// the man page with autocomplete + dead-code detection.

export type CapsuleType = "subsystem" | "adapter" | "template" | "bundle" | "library";

export interface Maintainer { name: string; email?: string; }

export interface Purpose {
  summary: string;
  owns?: string[];
  does_not_own?: string[];
}

export interface InterfaceProvides {
  kind: string;
  name: string;
  spec?: string;
  entrypoint?: string;
  payload_schema?: string;
  description?: string;
}

export interface InterfaceRequires {
  kind: string;
  name: string;
  from_capsule?: string;
  version?: string;
  description?: string;
}

export interface Interfaces {
  provides?: InterfaceProvides[];
  requires?: InterfaceRequires[];
}

export interface CapsuleDependency { name: string; version?: string; }

export interface Dependencies {
  capsules?: CapsuleDependency[];
  runtime?: Record<string, string>[];
}

export interface ExtensionPoint { id: string; where: string; contract: string; }

export interface AgentContext {
  summary_for_ai?: string;
  extension_points?: ExtensionPoint[];
  avoid?: string[];
  glossary?: Record<string, string>;
}

export interface Check {
  id: string;
  command: string;
  timeout_seconds?: number;
  proves?: string[];
  requires_capsules?: string[];
  cwd?: string;
  env?: Record<string, string>;
}

export interface Verification {
  health_checks?: Check[];
  functional_tests?: Check[];
  integration_tests?: Check[];
  invariants?: string[];
}

export interface CompatibilityEntry {
  capsule: string;
  versions: string;
  verification?: string;
}

export interface Compatibility { tested_with?: CompatibilityEntry[]; }

export interface Handoff {
  generated_at?: string;
  generated_by?: string;
  objective: string;
  completed?: string[];
  remaining?: string[];
  open_questions?: string[];
  next_agent_should?: string[];
  do_not_touch?: string[];
}

export interface Capsule {
  apiVersion: string;
  kind: "Capsule";
  name: string;
  version: string;
  type: CapsuleType;
  domain?: string;
  maintainers?: Maintainer[];
  purpose: Purpose;
  interfaces?: Interfaces;
  dependencies?: Dependencies;
  agent?: AgentContext;
  verification?: Verification;
  compatibility?: Compatibility;
  handoff?: Handoff;
}
