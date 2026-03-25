// Datadog RUM initialization for React app
// Replace with your actual values or use environment variables
import { datadogRum } from '@datadog/browser-rum';

datadogRum.init({
  applicationId: 'YOUR_APPLICATION_ID', // TODO: Replace with your Datadog RUM application ID
  clientToken: 'YOUR_CLIENT_TOKEN',     // TODO: Replace with your Datadog RUM client token
  site: 'datadoghq.com',
  service: 'open-defect-ui',
  env: 'local',
  version: '1.0.0',
  sampleRate: 100,
  trackInteractions: true,
  defaultPrivacyLevel: 'mask-user-input',
});

datadogRum.startSessionReplayRecording();
