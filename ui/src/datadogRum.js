// Datadog RUM initialization for React app
// Replace with your actual values or use environment variables
import { datadogRum } from '@datadog/browser-rum';
import { reactPlugin } from '@datadog/browser-rum-react';

datadogRum.init({
  applicationId: import.meta.env.VITE_DD_APPLICATION_ID,
  clientToken: import.meta.env.VITE_DD_CLIENT_TOKEN,
  site: 'datadoghq.com',
  service: 'open-defect-ui',
  env: 'local',
  version: '1.0.0',
  sampleRate: 100,
  sessionReplaySampleRate: 20,
  trackInteractions: true,
  defaultPrivacyLevel: 'mask-user-input',
  plugins: [reactPlugin({ router: true })],
});



datadogRum.startSessionReplayRecording();
