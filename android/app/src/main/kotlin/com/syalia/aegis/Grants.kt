package com.syalia.aegis

/**
 * What the phone lets its owner's agents do, decided here and never on the server (spec section 06). Slice 1 runs only the
 * sense class, which is allowed; the other classes and the approval sheet arrive with their operations.
 */
object Grants {
    enum class Cls { SENSE, ACT, FILES, APPS, PERSONAL }

    enum class Rule { ALLOW, ASK_SESSION, ASK_EVERY, DENY }

    val DEFAULTS = mapOf(
        Cls.SENSE to Rule.ALLOW,
        Cls.ACT to Rule.ASK_SESSION,
        Cls.FILES to Rule.ASK_SESSION,
        Cls.APPS to Rule.ASK_SESSION,
        Cls.PERSONAL to Rule.ASK_EVERY,
    )

    private val CLASS_OF = mapOf(
        "device.info" to Cls.SENSE,
        "sensor.list" to Cls.SENSE,
        "sensor.read" to Cls.SENSE,
        "compass.read" to Cls.SENSE,
    )

    fun classOf(op: String): Cls? = CLASS_OF[op]

    /** The rule for an operation, or null when the phone does not run it. */
    fun ruleFor(op: String, rules: Map<Cls, Rule> = DEFAULTS): Rule? = classOf(op)?.let { rules[it] ?: Rule.DENY }

    /** Whether ``op`` may run now without asking. Until the sheet exists, anything that would ask is denied. */
    fun allowedWithoutAsking(op: String, rules: Map<Cls, Rule> = DEFAULTS): Boolean = ruleFor(op, rules) == Rule.ALLOW
}
