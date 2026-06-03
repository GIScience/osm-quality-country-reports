import * as duckdb from "@duckdb/duckdb-wasm";
import type { AsyncDuckDB, AsyncDuckDBConnection } from "@duckdb/duckdb-wasm";

let dbInstance: AsyncDuckDB | null = null;
let dbConnection: AsyncDuckDBConnection | null = null;
let initPromise: Promise<{ db: AsyncDuckDB; conn: AsyncDuckDBConnection }> | null = null;

export async function initDuckDB(): Promise<{ db: AsyncDuckDB; conn: AsyncDuckDBConnection }> {
  if (dbInstance && dbConnection) {
    return { db: dbInstance, conn: dbConnection };
  }

  if (initPromise) {
    return initPromise;
  }

  initPromise = initDuckDBInternal();

  try {
    const result = await initPromise;
    return result;
  } catch (e) {
    initPromise = null;
    dbInstance = null;
    dbConnection = null;
    throw e;
  }
}

async function initDuckDBInternal(): Promise<{ db: AsyncDuckDB; conn: AsyncDuckDBConnection }> {
  const MAX_RETRIES = 3;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    let worker: Worker | null = null;
    let workerUrl: string | null = null;

    try {
      const JSDELIVR_BUNDLES = duckdb.getJsDelivrBundles();
      const bundle = await duckdb.selectBundle(JSDELIVR_BUNDLES);

      workerUrl = URL.createObjectURL(
        new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" })
      );

      worker = new Worker(workerUrl);
      const logger = new duckdb.ConsoleLogger();
      const db = new duckdb.AsyncDuckDB(logger, worker);
      await db.instantiate(bundle.mainModule, bundle.pthreadWorker);

      const conn = await db.connect();

      dbInstance = db;
      dbConnection = conn;

      return { db, conn };
    } catch (e) {
      console.warn(`DuckDB init attempt ${attempt + 1}/${MAX_RETRIES} failed:`, e);
      worker?.terminate();
      if (workerUrl) URL.revokeObjectURL(workerUrl);

      if (attempt < MAX_RETRIES - 1) {
        await new Promise(r => setTimeout(r, 500 * (attempt + 1)));
      }
    }
  }

  throw new Error("DuckDB initialization failed after multiple retries");
}

let fileCounter = 0;
const parquetFileCache = new Map<string, string>();

export async function registerParquetFile(parquetUrl: string, db: AsyncDuckDB): Promise<string> {
  const cached = parquetFileCache.get(parquetUrl);
  if (cached) return cached;

  const resp = await fetch(parquetUrl);
  const parquetData = await resp.arrayBuffer();
  
  fileCounter++;
  const fileName = `data_${fileCounter}.parquet`;
  await db.registerFileBuffer(fileName, new Uint8Array(parquetData));

  parquetFileCache.set(parquetUrl, fileName);
  
  return fileName;
}

export function clearParquetCache() {
  parquetFileCache.clear();
}


