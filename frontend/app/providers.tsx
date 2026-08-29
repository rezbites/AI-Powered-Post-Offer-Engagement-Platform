"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

/**
 * React Query provider.
 *
 * The client is created inside state rather than at module scope so it is not
 * shared across requests during server rendering - a shared client would leak
 * one user's cached data into another's page.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Recruiters leave this open all day; refetching on every window
            // focus would hammer the API for data that changes slowly.
            refetchOnWindowFocus: false,
            staleTime: 30_000,
            retry: 1,
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
