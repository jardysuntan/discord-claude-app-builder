import WidgetKit
import SwiftUI

struct AppEntry: TimelineEntry {
    let date: Date
    let appName: String
}

struct AppTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> AppEntry {
        AppEntry(date: Date(), appName: "KMPTemplate")
    }

    func getSnapshot(in context: Context, completion: @escaping (AppEntry) -> Void) {
        completion(AppEntry(date: Date(), appName: "KMPTemplate"))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<AppEntry>) -> Void) {
        let entry = AppEntry(date: Date(), appName: "KMPTemplate")
        let timeline = Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(3600)))
        completion(timeline)
    }
}

struct AppWidgetView: View {
    var entry: AppEntry

    var body: some View {
        VStack(spacing: 8) {
            Text(entry.appName)
                .font(.headline)
            Text(entry.date, style: .time)
                .font(.caption)
        }
        .containerBackground(.fill.tertiary, for: .widget)
    }
}

struct AppWidget: Widget {
    let kind: String = "AppWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: AppTimelineProvider()) { entry in
            AppWidgetView(entry: entry)
        }
        .configurationDisplayName("KMPTemplate")
        .description("A widget for KMPTemplate.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
