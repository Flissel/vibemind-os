import '../lib/loadenv';
import { db, mongoClient } from '../lib/mongodb';
import { ensureAllIndexes } from "../../src/infrastructure/mongodb/ensure-indexes";

async function main() {
    try {
        await ensureAllIndexes(db);
        console.log("Indexes ensured");
    } finally {
        // The shared client from `lib/mongodb` keeps an open connection, and
        // an open connection keeps the event loop alive: without this close
        // the script printed "Indexes ensured" and then hung forever, which
        // stalls any deployment that runs it as a step. The catalog loader
        // has always closed its client the same way.
        await mongoClient.close();
    }
}

main().catch((err) => {
    // eslint-disable-next-line no-console
    console.error(err);
    process.exit(1);
});
