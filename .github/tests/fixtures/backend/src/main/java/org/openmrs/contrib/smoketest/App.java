package org.openmrs.contrib.smoketest;

/** Trivial class so the smoke-test build has something to compile and package. */
public final class App {

    private App() {}

    public static String greeting() {
        return "smoke-test ok";
    }
}
