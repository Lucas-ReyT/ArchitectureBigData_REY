import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

export const API_BASE = "http://localhost:8000/api";

export const apiSlice = createApi({
  reducerPath: "api",
  baseQuery: fetchBaseQuery({ baseUrl: API_BASE }),
  endpoints: (builder) => ({
    searchEnterprises: builder.query({
      query: ({ q, scrapedOnly }) =>
        `/enterprises?q=${encodeURIComponent(q)}${scrapedOnly ? "&scraped_only=true" : ""}`,
    }),
    getEnterprise: builder.query({
      query: (num) => `/enterprises/${encodeURIComponent(num)}`,
    }),
    getDocuments: builder.query({
      query: (num) => `/enterprises/${encodeURIComponent(num)}/documents`,
    }),
    getRepresentatives: builder.query({
      query: (num) => `/enterprises/${encodeURIComponent(num)}/representatives`,
    }),
  }),
});

export const {
  useSearchEnterprisesQuery,
  useGetEnterpriseQuery,
  useGetDocumentsQuery,
  useGetRepresentativesQuery,
} = apiSlice;
