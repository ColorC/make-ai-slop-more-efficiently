'use strict'

const mineflayer = require('mineflayer')

const host = process.argv[2] || '127.0.0.1'
const port = Number(process.argv[3] || 25565)
const username = process.argv[4] || `ObservatoryOracle${process.pid}`
const timeoutMs = Number(process.argv[5] || 15000)

let finished = false
let bot

function finish(payload, code = 0) {
  if (finished) return
  finished = true
  try {
    if (bot) bot.quit('game-observatory read-only probe complete')
  } catch (_) {}
  process.stdout.write(`${JSON.stringify(payload)}\
`)
  setTimeout(() => process.exit(code), 50)
}

bot = mineflayer.createBot({
  host,
  port,
  username,
  auth: 'offline',
  hideErrors: true,
})

bot.once('spawn', () => {
  setTimeout(() => {
    const inventory = bot.inventory.items().map((item) => ({
      name: item.name,
      type: item.type,
      count: item.count,
      slot: item.slot,
    }))
    finish({
      ok: true,
      adapter: 'mineflayer-read-only-oracle',
      server: { host, port, version: bot.version },
      player: {
        username: bot.username,
        position: {
          x: bot.entity.position.x,
          y: bot.entity.position.y,
          z: bot.entity.position.z,
        },
        gameMode: bot.game?.gameMode || 'unknown',
        dimension: bot.game?.dimension || 'unknown',
      },
      inventory,
      heldItem: bot.heldItem ? bot.heldItem.name : null,
      readOnly: true,
    })
  }, 500)
})

bot.once('kicked', (reason) => finish({ ok: false, error: `kicked: ${String(reason)}` }, 2))
bot.once('error', (error) => finish({ ok: false, error: error.message }, 2))
setTimeout(() => finish({ ok: false, error: `timeout after ${timeoutMs}ms` }, 2), timeoutMs)