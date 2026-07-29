import { apiRequest, formDataRequestInit, jsonRequestInit } from '../../api/client';
import {
  repairCatalogSchema,
  repairListSchema,
  repairSchema,
  repairsMetaSchema,
  type Repair,
  type RepairCatalogItem,
  type RepairFormValues,
  type RepairsMeta,
} from './schemas';

export async function fetchRepairs(params: URLSearchParams) {
  const response = await apiRequest(`/repairs?${params.toString()}`, repairListSchema);
  return {
    repairs: response.data,
    meta: repairsMetaSchema.parse(response.meta),
  } satisfies { repairs: Repair[]; meta: RepairsMeta };
}

export async function fetchRepairCatalog(): Promise<RepairCatalogItem[]> {
  return (await apiRequest('/repairs/catalog?limit=200', repairCatalogSchema)).data;
}

export async function createRepair(values: RepairFormValues) {
  return (await apiRequest('/repairs', repairSchema, jsonRequestInit('POST', values))).data;
}

export async function updateRepair(id: string, values: RepairFormValues) {
  return (await apiRequest(`/repairs/${id}`, repairSchema, jsonRequestInit('PATCH', values))).data;
}

export async function archiveRepair(id: string) {
  return (
    await apiRequest(
      `/repairs/${id}`,
      repairSchema.pick({ id: true }).extend({ archived: repairSchema.shape.is_archived }),
      jsonRequestInit('DELETE'),
    )
  ).data;
}

export async function restoreRepair(id: string) {
  return (await apiRequest(`/repairs/${id}/restore`, repairSchema, jsonRequestInit('POST', {})))
    .data;
}

export async function changeRepairStatus(id: string, status: string, comment = '') {
  return (
    await apiRequest(
      `/repairs/${id}/status`,
      repairSchema,
      jsonRequestInit('POST', { status, comment }),
    )
  ).data;
}

export async function addRepairShipment(
  id: string,
  shipment: {
    direction: string;
    carrier: string;
    track_number: string;
    sent_at: string;
    status: string;
    received_at: string;
  },
) {
  return (
    await apiRequest(`/repairs/${id}/shipments`, repairSchema, jsonRequestInit('POST', shipment))
  ).data;
}

export async function uploadRepairAttachment(id: string, file: File) {
  const data = new FormData();
  data.append('attachments', file);
  return (
    await apiRequest(`/repairs/${id}/attachments`, repairSchema, formDataRequestInit('POST', data))
  ).data;
}
