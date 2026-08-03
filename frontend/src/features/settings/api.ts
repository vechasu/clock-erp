import { apiRequest, jsonRequestInit } from '../../api/client';
import { settingsSchema, type Settings } from './schemas';

export async function fetchSettings() {
  return (await apiRequest('/settings', settingsSchema)).data;
}

export async function updateSettings(values: Settings) {
  return (await apiRequest('/settings', settingsSchema, jsonRequestInit('PATCH', values))).data;
}
