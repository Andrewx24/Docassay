import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  main: [
    'intro',
    {
      type: 'category',
      label: 'Architecture',
      collapsed: false,
      items: [
        'architecture/overview',
        'architecture/repo-layout',
        'architecture/spec',
        'architecture/golden-sets',
        'architecture/scoring',
        'architecture/normalization',
        'architecture/adapters',
        'architecture/cli',
        'architecture/ci-gate',
        'architecture/verify',
        'architecture/parity',
        'architecture/packaging',
      ],
    },
    {
      type: 'category',
      label: 'Decisions (ADRs)',
      items: [
        'adr/monorepo',
        'adr/spec-first',
        'adr/two-native-packages',
        'adr/judge-not-parse',
        'adr/license',
      ],
    },
    {
      type: 'category',
      label: 'Implementation',
      collapsed: false,
      items: ['implementation/build-plan', 'implementation/agent-guide', 'implementation/definition-of-done'],
    },
  ],
};

export default sidebars;
