import React from 'react'
import SessionCompanion from '../cc_session/SessionCompanion'
import type { CcCompanionEntity } from './index'

export default function CcCompanionEditor({ entity }: { entity: CcCompanionEntity }) {
  return <SessionCompanion sessionId={entity.id} mode="tab" />
}
