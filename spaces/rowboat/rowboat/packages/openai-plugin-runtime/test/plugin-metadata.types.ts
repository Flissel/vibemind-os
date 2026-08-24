import type {
  NormalizedPluginComponent,
  PluginMetadata,
  PluginMetadataValue,
} from "../src/index.js";

const metadata: PluginMetadata = {
  displayName: "GitHub",
  nested: {
    enabled: true,
    labels: ["automation", "review"],
  },
};

const component: NormalizedPluginComponent = {
  id: "github:review",
  name: "github:review",
  kind: "skill",
  status: "available",
  metadata,
};

const metadataValues: readonly PluginMetadataValue[] = [
  component.metadata,
  "github",
  1,
  true,
  null,
];

// @ts-expect-error Metadata records are readonly.
metadata.displayName = "Other";

// @ts-expect-error Metadata arrays are readonly.
metadataValues.push("other");

// @ts-expect-error Undefined is not JSON-safe metadata.
const undefinedMetadata: PluginMetadataValue = undefined;

// @ts-expect-error BigInt is not JSON-safe metadata.
const bigintMetadata: PluginMetadataValue = 1n;

// @ts-expect-error Functions are not JSON-safe metadata.
const functionMetadata: PluginMetadataValue = () => "secret";
