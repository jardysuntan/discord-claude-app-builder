package com.jaredtan.kmptemplate

interface Platform {
    val name: String
}

expect fun getPlatform(): Platform